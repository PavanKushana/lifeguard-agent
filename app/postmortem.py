"""
postmortem.py
Write the postmortem after an incident: what happened, when, why, what fixed it, what's next.

Real teams write one after every outage, "blameless": it's about what happened, not who.
This one is built by plain code from what was recorded during the incident (tool outputs,
the final call, the human's decision, the verification), so nothing in it can be made up
and it costs no tokens.

Saved as markdown in results/incidents/. On Day 12 these files become the agent's memory:
"have we seen this before, and what fixed it?" That's why it only contains what the agent
found and did, never the fault's hidden truth.
"""

import re
from datetime import datetime
from pathlib import Path

INCIDENTS_DIR = Path("results") / "incidents"

# A log line from get_logs that shows something went wrong: "2026-10-21 18:13:35 ERROR: ...".
ERROR_LINE = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*\b(?:ERROR|FATAL|PANIC|Killed)\b.*)$", re.MULTILINE)


def first_error(evidence: list) -> tuple[datetime, str] | None:
    """The earliest error line in all the logs the agent read, with its time, or None if there was none."""
    found = []
    for entry in evidence:
        if entry.startswith("$ get_logs"):
            found += [(datetime.strptime(time, "%Y-%m-%d %H:%M:%S"), line) for time, line in ERROR_LINE.findall(entry)]
    return min(found) if found else None


# "Long-term", "Long term" or "Long‑term": models often write a non-breaking hyphen (‑)
# that looks like "-" but isn't, so any hyphen-like character counts.
LONG_TERM = r"long[\s\-‐-―]?term"


def long_term_fix(report: str) -> str | None:
    """The investigator's long-term fix: the line of its report that starts with 'Long-term'."""
    for line in report.splitlines():
        cleaned = line.strip().lstrip("-*• ").replace("**", "").replace("*", "")
        if re.match(LONG_TERM, cleaned, re.IGNORECASE):
            # Drop the label itself: "Long-term fix: store backups..." becomes "store backups...".
            return re.sub(rf"^{LONG_TERM}( fix)?:?\s*", "", cleaned, flags=re.IGNORECASE)
    return None


def steps_text(steps: list) -> list[str]:
    """Steps as numbered lines like '1. disable_job(job=db-backup)'."""
    lines = []
    for number, step in enumerate(steps, start=1):
        details = ", ".join(f"{key}={value}" for key, value in step.items() if key != "type")
        lines.append(f"{number}. `{step.get('type')}({details})`")
    return lines


def build_postmortem(incident: dict) -> str:
    """Turn everything recorded during one incident into a postmortem in markdown.

    `incident` holds: alert, started, times (when each stage finished), triage, report,
    final, evidence, usage, decision, done (the steps that ran) and verification.
    """
    final = incident["final"] or {}
    decision = incident["decision"] or {"approved": False, "note": "no fix options to choose from"}
    verification = incident["verification"]
    evidence = incident["evidence"]
    times = incident["times"]
    error = first_error(evidence)
    approved = decision.get("approved")
    option = decision.get("option") if approved else None
    searches = sum(1 for entry in evidence if entry.startswith("$ search_web"))

    lines = [f"# Postmortem: {incident['alert']}", "",
             f"**Date:** {incident['started']:%Y-%m-%d %H:%M}  ",
             f"**Result:** {verification['verdict']}  ",
             f"**Decision by:** {decision.get('by', '-')}  ",
             "*Blameless: this is about what happened, not who.*", ""]

    # Summary: the whole incident in a few sentences.
    lines += ["## Summary", ""]
    summary = f"Users reported: \"{incident['alert']}\" "
    if final.get("root_cause"):
        summary += f"Root cause: {final['root_cause']} "
    summary += (f"Fix applied: {option.get('title')}. " if option
                else "No fix was approved, so nothing was changed. ")
    summary += f"Result after checking the server: {verification['verdict']}."
    lines += [summary, ""]

    # Impact: what users saw and for how long.
    lines += ["## Impact", "", f"- What users saw: {incident['alert']}"]
    if error:
        lines.append(f"- First error in the logs: {error[0]:%H:%M:%S}, `{error[1]}`")
        if verification["verdict"] != "NOT FIXED" and times.get("verdict"):
            minutes = (times["verdict"] - error[0]).total_seconds() / 60
            lines.append(f"- From the first error to users OK again: about {minutes:.0f} minutes")
    if verification["verdict"] == "NOT FIXED":
        lines.append("- Still affecting users when this was written.")
    lines.append("")

    # Timeline: every stage with its time, so anyone can see how long each part took.
    lines += ["## Timeline", ""]
    if error:
        lines.append(f"- {error[0]:%H:%M:%S} First error in the logs")
    triage = incident.get("triage") or {}
    lines.append(f"- {incident['started']:%H:%M:%S} Alert received, investigation started "
                 f"(first guess: severity {triage.get('severity', '?')}, category {triage.get('category', '?')})")
    if times.get("report"):
        lines.append(f"- {times['report']:%H:%M:%S} Investigator's report done "
                     f"({len(evidence)} tool calls, {searches} web search{'es' if searches != 1 else ''})")
    if times.get("final_call") and final:
        blocked = len(final.get("blocked", []))
        lines.append(f"- {times['final_call']:%H:%M:%S} Final call: root cause and {len(final.get('options', []))} "
                     f"fix options" + (f" ({blocked} blocked by the safety check)" if blocked else ""))
    if decision.get("at"):
        lines.append(f"- {decision['at'][11:]} Decision by {decision.get('by')}: {decision['note']}")
    if times.get("apply"):
        lines.append(f"- {times['apply']:%H:%M:%S} Fix applied")
    if times.get("verdict"):
        lines.append(f"- {times['verdict']:%H:%M:%S} Server checked: {verification['verdict']}")
    lines.append("")

    # Root cause with the evidence it rests on.
    if final.get("root_cause"):
        lines += ["## Root cause", "", final["root_cause"], "",
                  f"Confidence: {final.get('confidence', '?')}. Evidence:"]
        lines += [f"- {fact}" for fact in final.get("evidence", [])]
        lines.append("")

    # Fix: what was done, and how to undo it if needed.
    lines += ["## Fix", ""]
    if option:
        lines += [f"**{option.get('title')}**", ""] + steps_text(option["steps"])
        lines += ["", f"How to undo: {option.get('undo', 'not given')}", ""]
        lines += ["What was done:"] + [f"- {line}" for line in incident["done"]]
    else:
        lines.append(f"No fix was applied ({decision['note']}).")
    offered = final.get("options", [])
    if offered:
        lines += ["", "Options that were offered:"]
        lines += [f"- {o.get('title')}{' (recommended)' if o.get('recommended') else ''}"
                  f"{' (approved)' if option is o else ''}" for o in offered]
    for item in final.get("blocked", []):
        lines.append(f"- BLOCKED by the safety check: {item['title']} ({item['reason']})")
    lines.append("")

    # Verification: the checks that decided the verdict.
    lines += ["## Verification", "", f"**{verification['verdict']}**", "", "Users OK?"]
    lines += [f"- {line}" for line in verification["users_ok"]]
    lines += ["", "Stays fixed?"] + [f"- {line}" for line in verification["stays_fixed"]]
    if verification.get("warnings"):
        lines += ["", "Watch out:"] + [f"- {line}" for line in verification["warnings"]]
    lines.append("")

    # Follow-ups: what is still open. A fix is not the end of an incident.
    lines += ["## Follow-ups", ""]
    if verification["verdict"] == "FIXED FOR NOW":
        lines.append("- The cause is still there, so this will come back. Plan a lasting fix.")
    elif verification["verdict"] == "NOT FIXED":
        lines.append("- Users are still affected. Pick a fix as soon as possible.")
    lines += [f"- Watch out: {warning}" for warning in verification.get("warnings", [])]
    if option:
        lines += [f"- Risk of the chosen fix: {con}" for con in option.get("cons", [])]
    later = long_term_fix(incident["report"])
    if later:
        lines.append(f"- Long-term fix suggested by the investigator: {later}")
    lines.append("")

    sources = final.get("sources", [])
    lines += ["## Sources (checked against the search results)", ""]
    lines += [f"- {link}" for link in sources] or ["- none"]
    lines += ["", "## Cost", "", f"{incident['usage'].summary()}", ""]
    return "\n".join(lines)


def save_postmortem(text: str, name: str, started: datetime) -> Path:
    """Save the postmortem as results/incidents/<date>_<time>_<name>.md and return the path."""
    INCIDENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = INCIDENTS_DIR / f"{started:%Y-%m-%d_%H%M}_{name}.md"
    path.write_text(text, encoding="utf-8")
    return path

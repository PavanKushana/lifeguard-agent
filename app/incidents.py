"""
incidents.py
An incident's life, whatever the visitor uses: start the agent, decide on a fix, write the postmortem.

The web API (main.py) and the Telegram bot (telegram_bot.py) both call these functions, so a fix
approved on the phone goes through exactly the same code as one approved on the website: the same
safety check right before approval, the same verdict, the same proof. One path, not two.

Other parts can listen to what happens ("subscribe"): the Telegram bot hears "options" (the agent
is done) and "decided" and messages the linked chat, without this file knowing anything about Telegram.

The words for the result ("Working again: placing orders.") are made here too, from the incident's
own data, so the website and Telegram always say the same thing.
"""

import logging
import re
import threading

from app import scoreboard
from app.agent import format_step, handle_incident
from app.approval import approve_option
from app.fixes import apply_fix, verify
from app.grader import grade
from app.llm import describe_error
from app.postmortem import build_postmortem
from app.sessions import Incident, Session
from app.validator import REQUEST

# uvicorn's own logger: what's written here shows up in the server's terminal (and Render's log).
log = logging.getLogger("uvicorn.error")

# Only one decision at a time: a double tap on "Approve" (or the website and the phone at the
# same moment) must not apply the same fix twice.
decide_lock = threading.Lock()


class IncidentError(Exception):
    """A request that can't be done right now, with an HTTP status and a sentence for the person."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


# ---------- Listeners: who wants to hear what happens ----------

_listeners: dict[str, list] = {"started": [], "options": [], "failed": [], "decided": []}


def subscribe(event: str, function) -> None:
    """Call function(session, incident) whenever `event` happens ("started", "options", "failed", "decided")."""
    _listeners[event].append(function)


def emit(event: str, session: Session, incident: Incident) -> None:
    """Tell every listener. A listener's error is logged, never passed on: a lost Telegram
    message must not break the incident itself."""
    for function in _listeners[event]:
        try:
            function(session, incident)
        except Exception:
            log.exception("A listener for %r failed", event)


# ---------- Start, decide, postmortem ----------

def check_can_investigate(session: Session) -> None:
    """Refuse (409) when the agent can't start: nothing is broken yet, or it already ran on this fault."""
    if session.fault is None:
        if session.build is not None and session.build.state == "building":
            raise IncidentError(409, "Your fault is still being built. Wait until GET /break/status says ready.")
        raise IncidentError(409, "Nothing is broken yet. Break the server first.")
    if session.incident is not None:
        raise IncidentError(409, "The agent already ran on this fault. Call POST /reset to start again.")


def start_investigation(session: Session) -> Incident:
    """Start the agent on the broken server in the background and return the new incident at once.

    The spending limit is checked by the caller first (it knows the visitor's address).
    """
    check_can_investigate(session)
    fault = session.fault
    session.incident = incident = Incident(alert=fault["alert"],
                                           truth={"title": fault.get("title", ""), "kind": session.fault_name,
                                                  **fault["truth"]})
    # daemon=True: if the API stops, it doesn't wait for a half-finished agent.
    threading.Thread(target=run_agent, args=(session, incident), daemon=True).start()
    emit("started", session, incident)
    return incident


def run_agent(session: Session, incident: Incident) -> None:
    """Run the whole agent (triage, investigation, final call) and store the results. Runs in the background.

    Any error is caught and recorded, so the page can show "failed" instead of waiting forever.
    """
    try:
        incident.result = handle_incident(session.server, incident.alert, incident.add_step)
        incident.state = "done"
    except Exception as error:  # e.g. Token Factory busy or down, or a missing API key
        log.exception("The agent stopped with an error")
        incident.error = describe_error(error)
        incident.state = "failed"
    # After a reset this incident belongs to nobody any more: nobody needs to hear about it.
    if session.incident is not incident:
        return
    if incident.state == "done" and incident.result.get("final"):
        emit("options", session, incident)
        grade_incident(session, incident)  # after the options went out, so nobody waits for it
    else:
        emit("failed", session, incident)


def grade_incident(session: Session, incident: Incident) -> None:
    """Stump the Agent: grade the agent's answer against the hidden truth (grader.py).

    The grade is kept on the incident and shown only after the decision. A visitor's own problem
    (a custom fault) also goes on the scoreboard. If grading fails, the incident goes on without it.
    """
    fault = session.fault
    try:
        incident.grade = grade(fault, incident.result["final"])
    except Exception:
        log.exception("Grading the answer failed")
        incident.grade = {"grade": None, "reason": "The answer couldn't be graded this time."}
        return
    if session.incident is incident and session.fault_name == "custom" and incident.grade.get("grade"):
        scoreboard.record(incident.grade["grade"], fault.get("title", ""))


def decide_incident(session: Session, option: int, approved_by: str) -> dict:
    """The human's decision: approve fix option 1, 2 or 3, or 0 to reject them all.

    An approved fix is applied and checked right away: FIXED, FIXED FOR NOW or NOT FIXED.
    Returns the check's result. approved_by says where the decision came from ("web visitor", "Telegram").
    """
    incident = session.incident
    # Two different situations, two different answers: "wait" would be wrong advice when nothing is running.
    if incident is None:
        raise IncidentError(409, "No investigation on this server. Break it and POST /investigate first.")
    if incident.state != "done":
        raise IncidentError(409, "There are no fix options yet. Wait until GET /incident says done.")
    final = incident.result["final"]
    options = final.get("options", []) if final else []
    if not 0 <= option <= len(options):
        raise IncidentError(400, f"There is no option {option}. Choose 1 to {len(options)}, or 0 to reject all.")

    with decide_lock:
        if incident.decision is not None:
            raise IncidentError(409, "A decision was already made for this incident. Call POST /reset to start again.")
        # The same function the terminal uses: it runs the safety check again before approving.
        incident.decision = approve_option(session.server, final, option, approved_by=approved_by)
        incident.done = apply_fix(session.server, incident.decision)

    if incident.done:
        incident.add_step("apply", "\n".join(f"{number}. {line}" for number, line in enumerate(incident.done, 1)))
    incident.verification = result = verify(session.server, session.fault)
    if result["verdict"] != "NOT FIXED":
        # The proof that customers are OK: the log lines the server writes once it has recovered
        # (the fault's "recovery", e.g. "INFO POST /api/orders 201": an order went through again).
        # A real on-call engineer checks the same way: not "it looks fine", but "orders succeed again".
        recovery_logs = session.fault.get("recovery", {}).get("logs", {})
        incident.proof = [f"{container}: {text}" for container, lines in recovery_logs.items() for _, text in lines]
    lines = [result["verdict"], f"Decision: {incident.decision['note']}", "", "Users OK?"]
    lines += [f"  {line}" for line in result["users_ok"]] + ["Stays fixed?"]
    lines += [f"  {line}" for line in result["stays_fixed"]]
    if result["warnings"]:
        lines += ["Watch out:"] + [f"  {line}" for line in result["warnings"]]
    incident.add_step("verdict", "\n".join(lines))
    emit("decided", session, incident)
    return result


def postmortem(session: Session) -> tuple[str, str]:
    """The incident report in markdown and a file name for it. Only after a decision."""
    incident = session.incident
    if incident is None or incident.decision is None:
        raise IncidentError(409, "No decision yet. Run the incident and POST /decide first.")
    if incident.verification is None:  # the fix is applied but the check is still running (a split second)
        raise IncidentError(409, "The fix is still being checked. Try again in a moment.")
    text = build_postmortem({**incident.result, "alert": incident.alert, "started": incident.started,
                             "times": incident.times, "decision": incident.decision, "done": incident.done,
                             "verification": incident.verification})
    return text, f"{incident.started:%Y-%m-%d_%H%M}_{session.fault_name}.md"


def final_call_view(final: dict) -> dict:
    """Ultra's fix options as separate fields, so a human can read them before deciding.

    The same facts as the "final_call" step, but that step is one long text for the timeline.
    Here every option has its own number, steps, pros, cons and risk: the web page turns them into
    option cards, the Telegram bot into a numbered list with Approve buttons.
    """
    options = [{"number": number,
                "title": option.get("title"),
                "recommended": bool(option.get("recommended")),
                "steps": [format_step(step) for step in option["steps"]],
                "pros": option["pros"],
                "cons": option["cons"],
                "undo": option["undo"],
                "can_be_undone": option["reversible"],
                "risk": option["risk"]}
               for number, option in enumerate(final["options"], start=1)]

    if any(option["recommended"] for option in options):
        why = final.get("why_recommended")
    else:
        # Same rule as the terminal: don't show praise for an option safety took away.
        why = ("Ultra's recommended option was blocked by the safety check, so none of these is "
               "recommended. Compare the cons before picking.")
    if options:
        # Same as the terminal's question: "1" alone reads better than "from 1 to 1".
        choices = "1" if len(options) == 1 else f"a number from 1 to {len(options)}"
        how_to_decide = f"POST /decide/{{option}} with {choices} to approve that option, or 0 to reject them all."
    else:
        how_to_decide = "No safe option is left. POST /decide/0 to reject, and fix it by hand."

    return {"root_cause": final.get("root_cause"),
            "confidence": final.get("confidence"),
            "options": options,
            "why_recommended": why,
            "blocked": final.get("blocked", []),  # each one {"title", "reason"}: the human sees what safety stopped
            "how_to_decide": how_to_decide}


# ---------- The result in words (website and Telegram) ----------

# What the shop's addresses are for, in a customer's words. An address that isn't listed (a custom
# fault can invent one) is shown as it is, e.g. "GET /api/search".
PAGE_NAMES = {
    "/api/orders": "placing orders",
    "/api/checkout": "checkout",
    "/api/cart": "the shopping cart",
    "/api/products": "product pages",
    "/products": "product pages",
    "/": "the home page",
}

# The disks in plain words, the same as on the page's Disks list.
DISK_NAMES = {"/": "main disk", "/var/lib/postgresql": "database disk"}


def parse_request(line: str) -> dict | None:
    """'api: INFO POST /api/orders 201 in 58ms' -> {container, method, path, code, success}, or None."""
    match = REQUEST.search(line)
    if not match:
        return None
    method, path, code = match.groups()
    # 2xx = the request worked (200 OK, 201 Created...).
    return {"container": line.split(":")[0], "method": method, "path": path, "code": code,
            "success": code.startswith("2")}


def proof_lines(proof: list[str]) -> list[dict]:
    """The recovery log lines for people: 'api: POST /api/orders answered 201 (success)'.

    Requests that worked come first: they're what customers feel. Other log lines stay as they are.
    """
    lines = []
    for line in proof:
        request = parse_request(line)
        if request is None:
            lines.append({"text": line, "success": False})
            continue
        ending = " (success)" if request["success"] else ""
        lines.append({"text": f"{request['container']}: {request['method']} {request['path']} "
                              f"answered {request['code']}{ending}", "success": request["success"]})
    return sorted(lines, key=lambda line: not line["success"])


def join_words(words: list[str]) -> str:
    """['a'] -> 'a', ['a', 'b'] -> 'a and b', ['a', 'b', 'c'] -> 'a, b and c'."""
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    return f"{', '.join(words[:-1])} and {words[-1]}"


def outcome(incident: Incident) -> dict:
    """What the fix changed for the customers, in words, built from this incident's own data.

    back:  what works again (the recovery logs; if they have no request in them, the checks that passed)
    kept:  for FIXED, why it stays fixed (the checks that passed, e.g. "job db-backup is off")
    still: for FIXED FOR NOW, what's still wrong (the checks that failed), so it will come back
    watch: side effects to keep an eye on, e.g. another disk nearly full after moving files there
    """
    result = {"back": "", "kept": "", "still": "", "watch": ""}
    checks = incident.verification
    if not checks:
        return result
    verdict = checks["verdict"]

    # The check lines read "OK  text" or "NO  text"; a line in brackets only says how they combine.
    def lines(name: str, start: str) -> list[str]:
        return [line[3:].strip() for line in checks[name] if line.startswith(start)]

    pages = []
    for line in incident.proof:
        request = parse_request(line)
        if request and request["success"]:
            name = PAGE_NAMES.get(request["path"], f"{request['method']} {request['path']}")
            if name not in pages:
                pages.append(name)
    if verdict != "NOT FIXED":
        passed = lines("users_ok", "OK")
        if pages:
            result["back"] = f"Working again: {join_words(pages)}."
        elif passed:
            result["back"] = f"Checked on the server: {join_words(passed)}."
    if verdict == "FIXED":
        result["kept"] = f"Stays fixed: {join_words(lines('stays_fixed', 'OK'))}."
    if verdict == "FIXED FOR NOW":
        result["still"] = f"The cause is still there: {join_words(lines('stays_fixed', 'NO'))}."

    # "/ is 90% full now" -> "the main disk (/) is 90% full now": the same words as the Disks list.
    warnings = []
    for text in checks["warnings"]:
        mount = text.split(" ")[0]
        warnings.append(f"the {DISK_NAMES[mount]} ({mount}){text[len(mount):]}" if mount in DISK_NAMES else text)
    if warnings:
        result["watch"] = f"Watch out: {join_words(warnings)}."
    return result

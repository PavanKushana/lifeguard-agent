"""
run_incident.py
Break the fake server with a preset fault and watch the agent investigate it, step by step.
At the end you pick one of Ultra's fix options and approve it, or reject them all.
An approved fix is applied to the fake server, which is then checked: FIXED, FIXED FOR NOW or NOT FIXED.
Last, a postmortem (what happened, why, what fixed it, what's next) is saved to results/incidents/.

Run from the project folder:
    python -m scripts.run_incident disk_full
    python -m scripts.run_incident db_out_of_memory

At the end it prints the hidden truth next to the agent's report, so you can judge by eye
whether it found the real cause. (An automatic grader comes on Day 10.)
Every run is also saved to results/runs/, so anyone can check what the agent really did.
"""

import getpass
import sys
from datetime import datetime
from pathlib import Path

from app import tools
from app.agent import handle_incident
from app.approval import approve_option
from app.faults import PRESETS, apply_fault
from app.fixes import apply_fix, verify
from app.llm import MODELS
from app.postmortem import build_postmortem, save_postmortem
from app.simulator import Server

# How each kind of step is labelled in the timeline.
LABELS = {
    "triage": "TRIAGE (Nano)",
    "thinking": "THINKING",
    "tool_call": "TOOL CALL",
    "tool_result": "RESULT",
    "report": "REPORT (Super)",
    "final_call": "FINAL CALL (Ultra)",
    "safety": "SAFETY CHECK",
    "rule": "RULE CHECK",
    "apply": "APPLYING THE FIX",
    "after": "AFTER THE FIX",
    "verdict": "VERDICT",  # not "RESULT": every tool output is already labelled RESULT
}

# Every run is saved here, one file per run, never overwritten.
RUNS_DIR = Path("results") / "runs"

# Lines of this run, collected so they can be saved to a file at the end.
transcript = []

# When each kind of step first happened ("report", "final_call", ...), for the postmortem's timeline.
step_times = {}


def say(text: str = "") -> None:
    """Print a line and remember it for the saved run file."""
    print(text)
    transcript.append(text)


def print_step(kind: str, text: str) -> None:
    """Print one step of the investigation as it happens, like a live timeline."""
    step_times.setdefault(kind, datetime.now())
    say(f"\n[{LABELS.get(kind, kind)}]")
    # Indent the text so the labels stand out and the timeline is easy to scan.
    for line in text.splitlines():
        say(f"    {line}")


def save_run(fault_name: str, started: datetime) -> Path:
    """Write this run to results/runs/<date>_<time>_<fault>.txt and return the path."""
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{started:%Y-%m-%d_%H%M}_{fault_name}.txt"
    # Header says when and with which models, so the file makes sense on its own.
    header = [f"Run: {fault_name} at {started:%Y-%m-%d %H:%M}",
              f"Models: {', '.join(f'{job}={model}' for job, model in MODELS.items())}",
              ""]
    # utf-8 because the models sometimes write characters Windows' default encoding can't save.
    path.write_text("\n".join(header + transcript) + "\n", encoding="utf-8")
    return path


def ask_number(question: str, highest: int) -> int:
    """Ask until the answer is a whole number from 0 to `highest`."""
    while True:
        answer = input(question).strip()
        if answer.isdigit() and 0 <= int(answer) <= highest:
            return int(answer)
        print(f"    Please type a number from 0 to {highest}.")


def ask_human(server: Server, final: dict | None) -> dict | None:
    """Let the person at the terminal pick a fix option and approve it. Returns the decision, or None."""
    if not final or not final.get("options"):
        say("\nNo safe fix options to choose from. A human has to decide what to do.")
        return None

    options = final["options"]
    say("\n" + "=" * 70)
    say("YOUR DECISION")
    for number, option in enumerate(options, start=1):
        star = " (recommended)" if option.get("recommended") else ""
        say(f"    {number}. {option.get('title')}{star}")
    say("    0. Reject all, change nothing")

    # 0 is the way out, not a fix, so the question names it separately.
    choices = "1" if len(options) == 1 else f"1-{len(options)}"
    number = ask_number(f"\nPick an option ({choices}), or 0 to reject all: ", len(options))
    say(f"Picked: {number}")
    if number:
        say("Steps that will run:")
        for step_number, step in enumerate(options[number - 1]["steps"], start=1):
            details = ", ".join(f"{key}={value}" for key, value in step.items() if key != "type")
            say(f"    {step_number}. {step.get('type')}({details})")
        # A second, explicit yes: picking alone is not approving.
        if input("Apply these steps? (y/n): ").strip().lower() != "y":
            say("Answer: no")
            number = 0
        else:
            say("Answer: yes")

    decision = approve_option(server, final, number, approved_by=getpass.getuser())
    say(f"\nDECISION: {decision['note']} (by {decision['by']} at {decision['at']})")
    return decision


def fix_and_verify(server: Server, fault: dict, decision: dict | None) -> tuple[list, dict]:
    """Apply the approved fix (if any), show the server afterwards, then always give the verdict.

    Returns (the steps that ran, the verification result), for the postmortem.

    A rejected fix gets a verdict too: nothing changed, so the problem is still there (NOT FIXED).
    The grader on Day 10 needs a verdict for every run, whatever the human decided.
    """
    approved = bool(decision and decision["approved"])
    done = []
    if approved:
        done = apply_fix(server, decision)
        print_step("apply", "\n".join(f"{number}. {line}" for number, line in enumerate(done, start=1)))

    # Grade first: if users are OK again, verify() also lets the server recover, so the
    # "after" view below shows the real state, errors gone or not.
    result = verify(server, fault)

    if approved:
        after = [("df -h", tools.check_disk(server)),
                 ("docker ps -a", tools.list_containers(server)),
                 ("crontab -l", tools.list_jobs(server)),
                 ("curl http://localhost/api/health",
                  tools.check_endpoint(server, "http://localhost/api/health")),
                 ("docker logs --tail 3 api", tools.get_logs(server, "api", lines=3)),
                 ("docker logs --tail 3 db", tools.get_logs(server, "db", lines=3))]
        print_step("after", "\n\n".join(f"$ {command}\n{output}" for command, output in after))

    lines = [result["verdict"]]
    if not approved:
        lines.append("No fix was approved, so nothing was changed.")
    lines += ["", "Users OK?"] + [f"  {line}" for line in result["users_ok"]]
    lines += ["Stays fixed?"] + [f"  {line}" for line in result["stays_fixed"]]
    if result["warnings"]:
        lines += ["Watch out:"] + [f"  {line}" for line in result["warnings"]]
    print_step("verdict", "\n".join(lines))
    return done, result


def main() -> None:
    """Break the server with the chosen fault, run the agent, then compare with the hidden truth."""
    if len(sys.argv) < 2 or sys.argv[1] not in PRESETS:
        print(f"Usage: python -m scripts.run_incident <fault>. Faults: {', '.join(PRESETS)}")
        return

    started = datetime.now()
    fault = PRESETS[sys.argv[1]]
    server = Server()
    apply_fault(server, fault)
    say(f"ALERT: {fault['alert']}")

    result = handle_incident(server, fault["alert"], print_step)
    decision = ask_human(server, result["final"])
    done, verification = fix_and_verify(server, fault, decision)

    # Written from what was recorded above only; the hidden truth below is never in it.
    postmortem = build_postmortem({**result, "alert": fault["alert"], "started": started, "times": step_times,
                                   "decision": decision, "done": done, "verification": verification})
    postmortem_path = save_postmortem(postmortem, sys.argv[1], started)

    say("\n" + "=" * 70)
    say("HIDDEN TRUTH (the agent never saw this):")
    say(f"    Root cause: {fault['truth']['root_cause']}")
    say(f"    Fix:        {fault['truth']['fix']}")
    say(f"\nCOST: {result['usage'].summary()}")
    # Plain print, so this line is not inside the saved file itself.
    print(f"\nSaved to {save_run(sys.argv[1], started)}")
    print(f"Postmortem: {postmortem_path}")


if __name__ == "__main__":
    main()

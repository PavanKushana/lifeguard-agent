"""
try_fault_maker.py
Type a fault idea, let Ultra build it, and see what the agent's tools would show.

The full path a custom fault takes: the guard checks the text, Ultra builds the fault,
the validator checks it (and sends problems back to Ultra once), then it's loaded into a
fresh server. The code checks what it can; still read the story by eye: is the alert free
of the cause, do the clues make sense?

Saved to results/custom_faults/<date>_<time>.txt (everything shown) and .json (the fault itself).

Run from the project folder:
    python -m scripts.try_fault_maker "the cache crashed"
"""

import json
import sys
from datetime import datetime
from pathlib import Path

from app.fault_maker import make_fault
from app.faults import apply_fault
from app.guard import check_fault_text
from app.llm import Usage
from app.simulator import Server
from app.tools import snapshot

RESULTS_DIR = Path("results") / "custom_faults"


def main() -> None:
    """Guard, build, load and show one custom fault, then save it all."""
    if len(sys.argv) < 2:
        print('Type a fault idea, for example: python -m scripts.try_fault_maker "the cache crashed"')
        return
    idea = " ".join(sys.argv[1:])
    usage = Usage()
    lines = []

    def say(text: str) -> None:
        """Print and keep the line for the saved file."""
        print(text)
        lines.append(text)

    say(f"Fault idea: {idea}")
    verdict = check_fault_text(idea, usage)
    say(f"\nGUARD: {'allowed' if verdict['allowed'] else 'refused'} ({verdict['checked_by']}): {verdict['reason']}")

    fault = None
    if verdict["allowed"]:
        say("\nUltra is building the fault...")
        fault, tries = make_fault(idea, usage)
        for number, attempt in enumerate(tries, start=1):
            if attempt["cleaned"]:
                say(f"\nCLEANED, try {number} (fixed in code, not sent back):")
                say("\n".join(f"  - {note}" for note in attempt["cleaned"]))
            problems = attempt["problems"]
            if problems:
                # Every try except the last one with problems was followed by a correction.
                sent_back = ", sent back to Ultra" if number < len(tries) else ""
                say(f"\nCHECKS, try {number}: {len(problems)} problem(s){sent_back}")
                say("\n".join(f"  - {problem}" for problem in problems))
            else:
                say(f"\nCHECKS, try {number}: all passed")

        if fault is None:
            say("\nREJECTED: Ultra couldn't build a fault that passes the checks. On the page: "
                "\"Couldn't build this fault, please try again or describe it differently.\"")
        else:
            say("\nTHE FAULT (hidden from the agent, except the alert):\n" + json.dumps(fault, indent=2))
            # The validator already loaded and tested it, so this can't fail.
            server = Server()
            apply_fault(server, fault)
            say(f"\nWHAT THE AGENT WILL SEE\nAlert: {fault['alert']}")
            for command, output in snapshot(server):
                say(f"\n$ {command}\n{output}")

    say(f"\n{usage.summary()}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}"
    path.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if fault:
        path.with_suffix(".json").write_text(json.dumps(fault, indent=2) + "\n", encoding="utf-8")
    elif verdict["allowed"] and tries and tries[-1]["fault"] is not None:
        # Rejected: keep Ultra's last version, so we can see what went wrong.
        rejected = path.with_name(path.name + "_rejected.json")
        rejected.write_text(json.dumps(tries[-1]["fault"], indent=2) + "\n", encoding="utf-8")
        print(f"Ultra's last version saved to {rejected}")
    print(f"Saved to {path}.txt")


if __name__ == "__main__":
    main()

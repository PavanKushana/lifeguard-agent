"""
try_fault_maker.py
Type a fault idea, let Ultra build it, and see what the agent's tools would show.

The full path a custom fault takes: the guard checks the text, Ultra builds the fault,
then it's loaded into a fresh server. Nothing is checked in code yet (that's the validator,
step 3), so read the result by eye: is the alert free of the cause, are the clues there,
do the times fit, can our 5 actions fix it?

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
        fault = make_fault(idea, usage)
        if fault is None:
            say("Ultra's reply could not be read as JSON, even after one retry.")
        else:
            say("\nTHE FAULT (hidden from the agent, except the alert):\n" + json.dumps(fault, indent=2))
            server = Server()
            try:
                apply_fault(server, fault)
            except Exception as error:
                # Anything can be wrong in AI-written data. The validator (step 3) will catch these
                # before loading; for now we just want to see what went wrong.
                say(f"\nCOULD NOT LOAD IT INTO THE SERVER: {type(error).__name__}: {error}")
            else:
                say(f"\nWHAT THE AGENT WILL SEE\nAlert: {fault.get('alert')}")
                for command, output in snapshot(server):
                    say(f"\n$ {command}\n{output}")

    say(f"\n{usage.summary()}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = RESULTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}"
    path.with_suffix(".txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if fault:
        path.with_suffix(".json").write_text(json.dumps(fault, indent=2) + "\n", encoding="utf-8")
    print(f"Saved to {path}.txt")


if __name__ == "__main__":
    main()

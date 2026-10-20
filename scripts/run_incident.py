"""
run_incident.py
Break the fake server with a preset fault and watch the agent investigate it, step by step.

Run from the project folder:
    python -m scripts.run_incident disk_full
    python -m scripts.run_incident db_out_of_memory

At the end it prints the hidden truth next to the agent's report, so you can judge by eye
whether it found the real cause. (An automatic grader comes on Day 10.)
Every run is also saved to results/runs/, so anyone can check what the agent really did.
"""

import sys
from datetime import datetime
from pathlib import Path

from app.agent import handle_incident
from app.faults import PRESETS, apply_fault
from app.llm import MODELS
from app.simulator import Server

# How each kind of step is labelled in the timeline.
LABELS = {
    "triage": "TRIAGE (Nano)",
    "thinking": "THINKING",
    "tool_call": "TOOL CALL",
    "tool_result": "RESULT",
    "report": "REPORT (Super)",
}

# Every run is saved here, one file per run, never overwritten.
RUNS_DIR = Path("results") / "runs"

# Lines of this run, collected so they can be saved to a file at the end.
transcript = []


def say(text: str = "") -> None:
    """Print a line and remember it for the saved run file."""
    print(text)
    transcript.append(text)


def print_step(kind: str, text: str) -> None:
    """Print one step of the investigation as it happens, like a live timeline."""
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

    say("\n" + "=" * 70)
    say("HIDDEN TRUTH (the agent never saw this):")
    say(f"    Root cause: {fault['truth']['root_cause']}")
    say(f"    Fix:        {fault['truth']['fix']}")
    say(f"\nCOST: {result['usage'].summary()}")
    # Plain print, so this line is not inside the saved file itself.
    print(f"\nSaved to {save_run(sys.argv[1], started)}")


if __name__ == "__main__":
    main()

"""
try_simulator.py
Break the fake server with a preset fault and print what every tool shows.
A quick way to check by eye that a fault looks realistic before the agent sees it.

Run from the project folder:
    python -m scripts.try_simulator                     (healthy server)
    python -m scripts.try_simulator disk_full
    python -m scripts.try_simulator db_out_of_memory
"""

import sys

from app import tools
from app.faults import PRESETS, apply_fault
from app.simulator import Server


def show(command: str, output: str) -> None:
    """Print one tool's output under the command it imitates, like a terminal."""
    print(f"\n$ {command}\n{output}")


def main() -> None:
    """Build a server, break it if a fault name was given, then run every tool on it."""
    server = Server()
    fault_id = sys.argv[1] if len(sys.argv) > 1 else None

    if fault_id:
        if fault_id not in PRESETS:
            print(f"Unknown fault '{fault_id}'. Choose one of: {', '.join(PRESETS)}")
            return
        fault = PRESETS[fault_id]
        apply_fault(server, fault)
        print(f"Fault: {fault['title']}")
        print(f"Alert (the only thing the agent gets): {fault['alert']}")
    else:
        print("No fault given, so the server is healthy.")

    # 15 log lines, so the first error of a fault isn't cut off by later noise.
    for command, output in tools.snapshot(server, log_lines=15):
        show(command, output)


if __name__ == "__main__":
    main()

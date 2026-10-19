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

    show("docker ps -a", tools.list_containers(server))
    show("docker stats --no-stream", tools.get_metrics(server))
    show("df -h", tools.check_disk(server))
    show("recent changes", tools.recent_changes(server))
    for url in server.endpoints:
        show(f"curl {url}", tools.check_endpoint(server, url))
    # 15 lines, so the first error of a fault isn't cut off by later noise.
    for name in server.containers:
        show(f"docker logs --tail 15 {name}", tools.get_logs(server, name, lines=15))


if __name__ == "__main__":
    main()

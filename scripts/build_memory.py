"""
build_memory.py
Makes the agent's starting memory (app/past_incidents.json) from the postmortems of our own test runs
(results/incidents/*.md, 21-23 Oct), so the demo remembers past incidents on its very first visit.

From each postmortem it keeps what an engineer would remember: what users saw, the root cause and its
evidence, the fix that was applied, how it ended, and the long-term follow-up. Only incidents whose fix
worked (FIXED or FIXED FOR NOW); for the same alert seen several times, only the newest one.

Run from the project folder:  python -m scripts.build_memory
"""

import json
import re
from pathlib import Path

SOURCE = Path("results") / "incidents"
TARGET = Path("app") / "past_incidents.json"


def section(text: str, heading: str) -> str:
    """The text under '## heading' up to the next '## ' heading."""
    match = re.search(rf"^## {re.escape(heading)}\s*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def parse(path: Path) -> dict | None:
    """One postmortem as a memory record, or None if its fix didn't work."""
    text = path.read_text(encoding="utf-8")
    result = re.search(r"^\*\*Result:\*\*\s*(.+?)\s*$", text, re.MULTILINE)
    if not result or result.group(1) not in ("FIXED", "FIXED FOR NOW"):
        return None
    cause_part = section(text, "Root cause")
    cause = cause_part.split("\n\n")[0].strip()
    evidence = [line[2:].strip() for line in cause_part.splitlines() if line.startswith("- ")]
    fix = re.search(r"\*\*(.+?)\*\*", section(text, "Fix"))
    follow_up = re.search(r"Long-term fix suggested by the investigator:\s*(.+)", section(text, "Follow-ups"))
    return {
        "when": re.search(r"^\*\*Date:\*\*\s*(.+?)\s*$", text, re.MULTILINE).group(1),
        "alert": re.search(r"^# Postmortem:\s*(.+)$", text, re.MULTILINE).group(1).strip(),
        "root_cause": cause,
        "evidence": evidence[:4],
        "fix": fix.group(1) if fix else "",
        "result": result.group(1),
        "follow_up": follow_up.group(1).strip() if follow_up else "",
        "source": f"test run ({path.name})",
    }


def main() -> None:
    """Read every postmortem, keep the newest working one per alert, write the memory file."""
    newest = {}
    for path in sorted(SOURCE.glob("*.md")):  # sorted by date, so a later one replaces an earlier one
        record = parse(path)
        if record:
            newest[record["alert"]] = record
    records = sorted(newest.values(), key=lambda record: record["when"])
    TARGET.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{len(records)} past incidents saved to {TARGET}:")
    for record in records:
        print(f"  {record['when']}  {record['result']:<13} {record['alert'][:70]}")


if __name__ == "__main__":
    main()

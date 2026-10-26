"""
make_eval_faults.py
Builds the fixed set of faults the evaluation runs on (results/eval_faults/), so every model setup
gets exactly the same 20 problems: the 6 presets (they live in app/faults.py) plus 14 custom faults.

The 14 custom faults: 10 picked from the ones Ultra built on 22 Oct (different kinds, no repeats,
each checked again with today's validator), plus 4 new ones for kinds we didn't have yet (Day 11
plan: variety; on 22 Oct most custom faults came out as "bad api deploy"). Then the same 4 ideas
are built once more with Super as the fault maker, only to compare speed and how often it passes
the checks (Day 11 plan: Ultra took 8-140 s per fault, is Super good enough and faster?).

Run from the project folder:  python -m scripts.make_eval_faults
It writes results/eval_faults/*.json and results/fault_maker_speed_<date>.md.
"""

import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from app import llm
from app.fault_maker import make_fault
from app.llm import Usage
from app.validator import validate_fault

SAVED = Path("results") / "custom_faults"
OUT = Path("results") / "eval_faults"

# Built by Ultra on 22 Oct: one of each kind, no near-copies (several ideas were built 3 times).
PICKED = ["2026-10-22_1857", "2026-10-22_1901", "2026-10-22_1904", "2026-10-22_1942", "2026-10-22_2010",
          "2026-10-22_2232", "2026-10-22_2234", "2026-10-22_2235", "2026-10-22_2238", "2026-10-22_1912"]
# Used instead if one of the picked ones no longer passes today's checks.
SPARE = ["2026-10-22_2244", "2026-10-22_1910", "2026-10-22_1946", "2026-10-22_1914", "2026-10-22_1947"]

# Kinds the saved set doesn't have yet. Each one can be fixed with the 5 actions on the menu.
NEW_IDEAS = [
    "a runaway export job runs every minute and fills the database disk",
    "someone lowered the api's memory limit by mistake and it keeps getting killed",
    "the web server (nginx) was killed once and didn't come back up (it has no restart policy)",
    "a new nginx version for web returns errors",
]


def slug(text: str) -> str:
    """'Cache out of memory after Redis upgrade' -> 'cache_out_of_memory_after_redis_upgrade' (for file names)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]


def save(number: int, fault: dict) -> Path:
    """Save one fault of the set as results/eval_faults/c<number>_<title>.json, with that name as its id."""
    fault["id"] = f"c{number:02d}_{slug(fault['title'])}"
    path = OUT / f"{fault['id']}.json"
    path.write_text(json.dumps(fault, indent=2) + "\n", encoding="utf-8")
    return path


def pick_saved() -> list[dict]:
    """The 10 saved faults, each checked again (the validator has learned new checks since 22 Oct)."""
    picked = []
    for name in PICKED + SPARE:
        if len(picked) == len(PICKED):
            break
        fault = json.loads((SAVED / f"{name}.json").read_text(encoding="utf-8"))
        problems = validate_fault(fault)
        if problems:
            print(f"  skipped {name} ({fault.get('title')}): {problems[0]}")
            continue
        picked.append(fault)
        print(f"  kept {name}: {fault['title']} ({fault['truth']['service']}, {fault['truth']['category']})")
    return picked


def build(idea: str, maker: str) -> dict:
    """Build one fault from an idea with the given model as the fault maker. Returns the fault and how it went."""
    llm.MODELS["fault_maker"] = maker
    usage = Usage()
    start = time.perf_counter()
    try:
        fault, tries = make_fault(idea, usage)
        error = None
    except Exception as failure:  # Token Factory busy or down: record it and go on with the next one
        fault, tries, error = None, [], llm.describe_error(failure)
    return {"idea": idea, "maker": maker, "fault": fault, "tries": len(tries), "seconds": time.perf_counter() - start,
            "cost": usage.cost(), "passed": fault is not None, "error": error,
            "problems": tries[-1]["problems"][:3] if tries and fault is None else []}


def main() -> None:
    """Keep 10 saved faults, build 4 new ones with Ultra, then the same 4 with Super for the speed comparison."""
    OUT.mkdir(parents=True, exist_ok=True)
    print("Saved faults from 22 Oct, checked again:")
    faults = pick_saved()

    print("\nNew faults, built by Ultra:")
    ultra_runs = []
    for idea in NEW_IDEAS:
        run = build(idea, llm.ULTRA)
        ultra_runs.append(run)
        title = run["fault"]["title"] if run["passed"] else f"FAILED ({run['error'] or run['problems']})"
        print(f"  {run['seconds']:5.0f} s, {run['tries']} tries: {title}")
        if run["passed"]:
            faults.append(run["fault"])

    for number, fault in enumerate(faults, start=1):
        print(f"  saved {save(number, fault)}")

    print("\nThe same ideas, built by Super (speed comparison only, not part of the set):")
    super_runs = []
    for idea in NEW_IDEAS:
        run = build(idea, llm.SUPER)
        super_runs.append(run)
        title = run["fault"]["title"] if run["passed"] else f"FAILED ({run['error'] or run['problems']})"
        print(f"  {run['seconds']:5.0f} s, {run['tries']} tries: {title}")
        if run["passed"]:
            (OUT / "_super_made").mkdir(exist_ok=True)
            (OUT / "_super_made" / f"{slug(run['fault']['title'])}.json").write_text(
                json.dumps(run["fault"], indent=2) + "\n", encoding="utf-8")
    llm.MODELS["fault_maker"] = llm.ULTRA

    # The comparison as a small table, for the notes and the README.
    lines = [f"# Fault maker: Ultra vs Super ({datetime.now():%Y-%m-%d})", "",
             "The same 4 ideas, built once by each model, checked by validator.py (up to 2 tries each).", "",
             "| Idea | Ultra | Super |", "|---|---|---|"]
    for ultra, superb in zip(ultra_runs, super_runs):
        cell = lambda run: (f"{'passed' if run['passed'] else 'failed'}, {run['seconds']:.0f} s, "
                            f"{run['tries']} tries, ${run['cost']:.3f}")
        lines.append(f"| {ultra['idea']} | {cell(ultra)} | {cell(superb)} |")
    for name, runs in (("Ultra", ultra_runs), ("Super", super_runs)):
        passed = sum(run["passed"] for run in runs)
        seconds = sum(run["seconds"] for run in runs) / len(runs)
        lines.append(f"\n{name}: {passed} of {len(runs)} passed, {seconds:.0f} s on average.")
    report = Path("results") / f"fault_maker_speed_{datetime.now():%Y-%m-%d}.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n{len(faults)} custom faults in {OUT}; speed comparison in {report}")
    sys.exit(0 if len(faults) == 14 else 1)


if __name__ == "__main__":
    main()

"""
run_eval.py
How good is the agent, in numbers? Runs it on the same 20 faults with three model setups and grades
every answer against the fault's hidden truth (grader.py).

The faults: the 6 presets plus the 14 custom faults in results/eval_faults/ (made by make_eval_faults.py).
The setups (the Day 11 comparison):
- full:  Nano takes the first look, Super investigates, Ultra makes the final call (what the demo uses)
- nano:  Nano does all three jobs
- super: Super does all three jobs
The grader is the same for every setup (Super with a fixed checklist), so the grades compare fairly.

For each run it saves: the grade (right / partly / wrong), whether the recommended fix really works
(tried on a copy of the broken server), how many of the offered fixes work, time, model calls, tokens
and the estimated cost. Plus the quirks the Day 11 plan asked to measure: does the first look say
"check web" too often, do options add a restart the real fix doesn't need, and with the full database
disk, which backup gets moved (the older one or the newest one).

Run from the project folder (the setups can run at the same time, each in its own window):
    python -m scripts.run_eval full
    python -m scripts.run_eval nano
    python -m scripts.run_eval super
    python -m scripts.run_eval report      (writes results/eval_<date>.md from the saved runs)
Each finished run is saved as results/eval/<setup>/<fault>.json, so a stopped run picks up where it left off.
"""

import json
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from app import llm
from app.agent import handle_incident
from app.faults import PRESETS, apply_fault
from app.grader import grade, recommended_number
from app.llm import NANO, SUPER, ULTRA, Usage
from app.simulator import Server

SETUPS = {
    "full": {"triage": NANO, "investigator": SUPER, "final_call": ULTRA},
    "nano": {"triage": NANO, "investigator": NANO, "final_call": NANO},
    "super": {"triage": SUPER, "investigator": SUPER, "final_call": SUPER},
}
FAULTS_DIR = Path("results") / "eval_faults"
OUT = Path("results") / "eval"


def eval_faults() -> list[dict]:
    """The 20 faults: the 6 presets first, then the 14 custom ones."""
    faults = list(PRESETS.values())
    for path in sorted(FAULTS_DIR.glob("c*.json")):
        faults.append(json.loads(path.read_text(encoding="utf-8")))
    return faults


def moved_backup(fault: dict, final: dict | None) -> str | None:
    """Database disk full only: which backup the recommended option moves, the older or the newest one.

    A known quirk (Day 11 plan): Ultra sometimes moved the newest backup, the one a restore would need.
    """
    if fault["id"] != "disk_full" or not final or not final.get("options"):
        return None
    server = Server()
    apply_fault(server, fault)
    backups = sorted(server.items_under("/var/lib/postgresql/backups"))  # the names hold the date: oldest first
    option = final["options"][recommended_number(final) - 1]
    for step in option.get("steps", []):
        if step.get("type") == "move_files":
            path = str(step.get("from_path", "")).rstrip("/")
            return "older" if path == backups[0] else "newest" if path == backups[-1] else "other"
    return "none moved"


def restarts_not_needed(fault: dict, final: dict | None) -> int:
    """How many offered options restart something although the real fix doesn't mention a restart."""
    if not final or "restart" in fault["truth"].get("fix", "").lower():
        return 0
    return sum(any(step.get("type") == "restart" for step in option.get("steps", []))
               for option in final.get("options", []))


def run_one(fault: dict, setup: str) -> dict:
    """Run the agent once on a fresh broken server, grade the answer, and return everything worth keeping."""
    llm.MODELS.update(SETUPS[setup])
    server = Server()
    apply_fault(server, fault)
    start = time.perf_counter()
    try:
        result = handle_incident(server, fault["alert"], lambda kind, text: None)
        error = None
    except Exception as failure:  # a busy or broken model call: the run counts, with its error
        result, error = None, llm.describe_error(failure)
    seconds = time.perf_counter() - start
    final = result["final"] if result else None
    usage = result["usage"] if result else Usage()
    grader_usage = Usage()
    try:
        graded = grade(fault, final, grader_usage)
    except Exception as failure:
        graded = {"grade": None, "reason": f"grading failed: {llm.describe_error(failure)}", "fix": None,
                  "option_results": []}
    truth = fault["truth"]
    return {
        "fault": fault["id"], "title": fault["title"], "kind": "preset" if fault["id"] in PRESETS else "custom",
        "setup": setup, "service": truth["service"], "category": truth["category"],
        "error": error, "seconds": round(seconds, 1), "calls": usage.calls,
        "tokens_in": usage.tokens_in, "tokens_out": usage.tokens_out, "cost": round(usage.cost(), 5),
        "grade": graded.get("grade"), "where": graded.get("where"), "what": graded.get("what"),
        "why": graded.get("why"), "reason": graded.get("reason"),
        "fix": graded.get("fix"), "option_results": graded.get("option_results", []),
        "options": len(final["options"]) if final else 0, "blocked": len(final.get("blocked", [])) if final else 0,
        # The titles too (added after the 26 Oct run), so a fix that didn't work can be read, not only counted.
        "option_titles": [str(option.get("title", "")) for option in final["options"]] if final else [],
        "recommended": recommended_number(final) if final and final.get("options") else None,
        "root_cause": final.get("root_cause") if final else None, "truth": truth["root_cause"],
        "check_first": str((result or {}).get("triage", {}).get("check_first", "")),
        "restarts_not_needed": restarts_not_needed(fault, final), "moved_backup": moved_backup(fault, final),
        "grader_cost": round(grader_usage.cost(), 5),
    }


def run_setup(setup: str) -> None:
    """Run one setup on every fault that has no saved run yet."""
    folder = OUT / setup
    folder.mkdir(parents=True, exist_ok=True)
    faults = eval_faults()
    for number, fault in enumerate(faults, start=1):
        path = folder / f"{fault['id']}.json"
        if path.exists():
            continue
        record = run_one(fault, setup)
        path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"[{number}/{len(faults)}] {setup} {fault['id']}: {record['grade']}, fix {record['fix']}, "
              f"{record['seconds']:.0f} s, ${record['cost']:.3f}" + (f", ERROR {record['error']}" if record["error"] else ""),
              flush=True)


def percent(part: int, whole: int) -> str:
    """'12 of 20 (60%)'."""
    return f"{part} of {whole} ({part / whole:.0%})" if whole else "0 of 0"


def report() -> Path:
    """Write results/eval_<date>.md: the comparison table, the full setup per fault, and the quirks."""
    runs = {setup: [json.loads(path.read_text(encoding="utf-8")) for path in sorted((OUT / setup).glob("*.json"))]
            for setup in SETUPS if (OUT / setup).exists()}
    faults = {fault["id"]: fault for fault in eval_faults()}
    lines = [f"# Evaluation, {datetime.now():%Y-%m-%d}", "",
             f"{len(faults)} faults (6 presets, {len(faults) - 6} custom faults built by Ultra), each run once per setup. "
             "Every answer graded against the fault's hidden truth by the same grader (Super, fixed checklist: right part, "
             "right problem, right trigger). Every offered fix tried on a copy of the broken server. Costs estimated "
             "from Token Factory's list prices.", "",
             "| Setup | Right cause | Partly | Wrong | Recommended fix works | Offered fixes that work | Avg time | Avg cost |",
             "|---|---|---|---|---|---|---|---|"]
    names = {"full": "Nano + Super + Ultra (the demo)", "nano": "Nano alone", "super": "Super alone"}
    for setup, done in runs.items():
        grades = Counter(run["grade"] for run in done)
        fixes = sum(run["fix"] in ("FIXED", "FIXED FOR NOW") for run in done)
        offered = [result for run in done for result in run["option_results"]]
        working = sum(result in ("FIXED", "FIXED FOR NOW") for result in offered)
        seconds = sum(run["seconds"] for run in done) / max(1, len(done))
        cost = sum(run["cost"] for run in done) / max(1, len(done))
        lines.append(f"| {names[setup]} | {percent(grades['right'], len(done))} | {grades['partly']} | "
                     f"{grades['wrong'] + grades[None]} | {percent(fixes, len(done))} | {percent(working, len(offered))} | "
                     f"{seconds:.0f} s | ${cost:.3f} |")

    if "full" in runs:
        lines += ["", "## The demo's setup, fault by fault", "",
                  "| Fault | Kind | Grade | Recommended fix | Options (working) | Time | Cost |", "|---|---|---|---|---|---|---|"]
        for run in runs["full"]:
            working = sum(result in ("FIXED", "FIXED FOR NOW") for result in run["option_results"])
            lines.append(f"| {run['title']} | {run['kind']} | {run['grade']} | {run['fix']} | "
                         f"{run['options']} ({working}) | {run['seconds']:.0f} s | ${run['cost']:.3f} |")

    lines += ["", "## Grades side by side", "", "| Fault | " + " | ".join(names[s] for s in runs) + " |",
              "|---|" + "---|" * len(runs)]
    for fault_id, fault in faults.items():
        cells = []
        for setup in runs:
            run = next((run for run in runs[setup] if run["fault"] == fault_id), None)
            cells.append(f"{run['grade']} / {run['fix']}" if run else "not run")
        lines.append(f"| {fault['title']} | " + " | ".join(cells) + " |")

    lines += ["", "## Known quirks (Day 11 plan)", ""]
    for setup, done in runs.items():
        web_first = sum(run["check_first"].strip().lower().startswith("web") for run in done)
        restarts = sum(run["restarts_not_needed"] for run in done)
        backups = Counter(run["moved_backup"] for run in done if run["moved_backup"])
        lines.append(f"- {names[setup]}: first look said \"check web\" first in {percent(web_first, len(done))} runs; "
                     f"{restarts} offered options restart something the real fix doesn't need; "
                     f"database disk full moved: {dict(backups) or 'nothing'}.")
    errors = [f"{run['setup']} {run['fault']}: {run['error']}" for done in runs.values() for run in done if run["error"]]
    if errors:
        lines += ["", "## Runs that stopped with an error", ""] + [f"- {error}" for error in errors]
    total = sum(run["cost"] + run.get("grader_cost", 0) for done in runs.values() for run in done)
    lines += ["", f"Whole evaluation: {sum(len(done) for done in runs.values())} runs, about ${total:.2f} "
                  "including the grading."]
    path = Path("results") / f"eval_{datetime.now():%Y-%m-%d}.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command in SETUPS:
        run_setup(command)
    elif command == "report":
        print(f"Saved {report()}")
    else:
        print(__doc__)

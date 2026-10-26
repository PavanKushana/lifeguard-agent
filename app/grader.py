"""
grader.py
How good was the agent's answer? Compares it with the fault's hidden truth, the same way every time.

The same fixed checklist for every fault and every model, so the grading is fair:
- where: does the diagnosis blame the part that's really broken (db, api, web or cache)?
- what:  does it name the right problem (disk full, out of memory, crash loop, wrong password...)?
- why:   does it name what started it (the backup job, the new version, the cron job...)?
A model answers the three questions. It sees only the hidden truth and the diagnosis, never which
setup wrote it. Plain code then turns the answers into the grade, so the grade means the same every
time: right = where + what + why, partly = where or what, wrong = neither. As a guard, "where" only
counts if the diagnosis names that part at all (checked in code, not by the model).

Then the fix: every option the agent offered is applied to a fresh copy of the broken server and
checked like a real decision (FIXED, FIXED FOR NOW or NOT FIXED), with the same checks that run after
a human's approval. That says whether the agent's recommended fix would really have worked.

Used by scripts/run_eval.py (the evaluation on 20 faults) and by "Stump the Agent" on the page, which
shows the hidden truth next to the agent's answer, with this grade, after the visitor's decision.
"""

import re

from app.agent import parse_json
from app.approval import approve_option
from app.faults import apply_fault
from app.fixes import apply_fix, verify
from app.llm import Usage, ask
from app.simulator import Server

# The words that name each part, as an engineer might write them: "PostgreSQL" means the db.
PART_WORDS = {
    "db": r"db|database|postgres(?:ql)?|pg_\w+",
    "api": r"api|app|application|backend|python|shopapp|flask|gunicorn|uvicorn",
    "web": r"web|nginx|frontend|front-end|proxy|reverse proxy",
    "cache": r"cache|redis",
}

GRADER_PROMPT = """You grade an on-call engineer's diagnosis against what really happened. Be strict and fair:
judge the meaning, not the wording.

Answer three questions with true or false:
- where: does the diagnosis blame the same broken part? A part that only shows the symptom (for example
  the web server returning errors because the real problem is in the database) does not count.
- what: does it name the same problem, for example disk full, out of memory, crash loop, wrong password,
  missing package, slow query, a job running too often? Different words for the same problem count.
- why: does it name what started it, for example the new backup job, the deploy of a new version, a cron
  job, a changed setting, a lowered memory limit? If what really happened has no trigger beyond the
  problem itself (a one-off crash), answer true when the diagnosis also sees it as a one-off.

Reply with ONLY this JSON, no other text:
{"where": true or false, "what": true or false, "why": true or false, "reason": "one short sentence"}"""


def names_part(diagnosis: str, part: str) -> bool:
    """True if the diagnosis mentions the part at all ("PostgreSQL" counts for db, "Redis" for cache)."""
    pattern = PART_WORDS.get(part, re.escape(part))
    return re.search(rf"\b(?:{pattern})\b", diagnosis, re.IGNORECASE) is not None


def grade_from(where: bool, what: bool, why: bool) -> str:
    """The grade from the checklist: right, partly or wrong. Worked out in code, so it means the same every time."""
    if where and what and why:
        return "right"
    if where or what:
        return "partly"
    return "wrong"


def judge(truth: dict, diagnosis: str, usage: Usage) -> dict | None:
    """Ask the grader model the three checklist questions. None if its answer can't be read, even after one retry."""
    messages = [
        {"role": "system", "content": GRADER_PROMPT},
        {"role": "user", "content": "What really happened (hidden from the engineer):\n"
                                    f"Broken part: {truth.get('service')}\n"
                                    f"Problem type: {truth.get('category')}\n"
                                    f"Root cause: {truth.get('root_cause')}\n\n"
                                    f"The engineer's diagnosis:\n{diagnosis}"},
    ]
    for _ in range(2):
        reply = ask("grader", messages, usage, max_tokens=6000)
        text = reply.content or ""
        try:
            answer = parse_json(text)
            if all(isinstance(answer.get(key), bool) for key in ("where", "what", "why")):
                return answer
        except ValueError:
            pass
        messages += [{"role": "assistant", "content": text},
                     {"role": "user", "content": "Reply with ONLY the JSON object, with true or false for where, what and why."}]
    return None


def try_options(fault: dict, final: dict) -> list[str]:
    """Apply each offered option to a fresh copy of the broken server; the verdict of each, in order.

    A fresh copy every time, so one option's changes never help the next one. The safety check runs
    again inside approve_option, exactly as for a human's decision.
    """
    verdicts = []
    for number in range(1, len(final.get("options", [])) + 1):
        server = Server()
        apply_fault(server, fault)
        decision = approve_option(server, final, number, approved_by="grader")
        apply_fix(server, decision)
        verdicts.append(verify(server, fault)["verdict"])
    return verdicts


def recommended_number(final: dict) -> int:
    """Which option the agent recommended (1, 2, 3); the first one if the recommended one was blocked."""
    for number, option in enumerate(final.get("options", []), start=1):
        if option.get("recommended"):
            return number
    return 1


def grade(fault: dict, final: dict | None, usage: Usage | None = None) -> dict:
    """Grade the agent's final call against the fault's hidden truth.

    Returns {"grade": "right" / "partly" / "wrong" (None if the grader's answer couldn't be read),
    "where", "what", "why" (the checklist), "reason" (one sentence), "fix" (the verdict of the
    recommended option, None without options), "option_results" (the verdict of every option)}.
    """
    usage = usage if usage is not None else Usage()
    if not final:
        return {"grade": "wrong", "where": False, "what": False, "why": False,
                "reason": "The agent gave no usable answer.", "fix": None, "option_results": []}

    truth = fault["truth"]
    diagnosis = str(final.get("root_cause", ""))
    option_results = try_options(fault, final)
    fix = option_results[recommended_number(final) - 1] if option_results else None
    answer = judge(truth, diagnosis, usage)
    if answer is None:
        return {"grade": None, "where": None, "what": None, "why": None,
                "reason": "The grader's answer couldn't be read.", "fix": fix, "option_results": option_results}

    where = answer["where"] and names_part(diagnosis, truth.get("service", ""))
    return {"grade": grade_from(where, answer["what"], answer["why"]),
            "where": where, "what": answer["what"], "why": answer["why"],
            "reason": str(answer.get("reason", "")).strip(), "fix": fix, "option_results": option_results}

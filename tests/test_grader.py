"""
test_grader.py
Checks the grader (grader.py): the checklist turns into the same grade every time, the part check in
code catches a diagnosis that blames the wrong part, and every fix option is tried on a fresh copy of
the broken server.

No AI: the grader model is replaced by a fake that answers the way it would.
Run all tests with:  python -m pytest
"""

from types import SimpleNamespace

from app import grader
from app.faults import PRESETS, apply_fault
from app.simulator import Server
from app.validator import find_fix


def fake_judge(monkeypatch, *answers):
    """Make the grader model answer with these texts, one per call."""
    replies = iter(answers)
    monkeypatch.setattr(grader, "ask", lambda job, messages, usage, max_tokens=4000: SimpleNamespace(content=next(replies)))


def disk_full_final() -> dict:
    """An answer to "Database disk full" with one option: the fix our own code finds (it works)."""
    fault = PRESETS["disk_full"]
    server = Server()
    apply_fault(server, fault)
    steps = find_fix(fault, server)[0]
    return {"root_cause": "The new backup job filled the PostgreSQL disk.",
            "options": [{"title": "Free space and stop the job", "steps": steps, "recommended": True}]}


def test_grade_from_checklist():
    assert grader.grade_from(True, True, True) == "right"
    assert grader.grade_from(True, False, False) == "partly"
    assert grader.grade_from(False, True, False) == "partly"
    assert grader.grade_from(False, False, True) == "wrong"


def test_names_part():
    assert grader.names_part("PostgreSQL ran out of disk space", "db")
    assert grader.names_part("Redis crashed after the upgrade", "cache")
    assert grader.names_part("nginx can't reach the upstream", "web")
    assert not grader.names_part("The API returns 500 errors", "db")


def test_every_option_is_tried_on_a_fresh_copy():
    """Option 2 alone (a restart) doesn't free any space; option 1's changes must not help it."""
    final = disk_full_final()
    final["options"].append({"title": "Restart the database", "steps": [{"type": "restart", "container": "db"}]})
    assert grader.try_options(PRESETS["disk_full"], final) == ["FIXED", "NOT FIXED"]


def test_right_answer(monkeypatch):
    fake_judge(monkeypatch, '{"where": true, "what": true, "why": true, "reason": "Same cause."}')
    result = grader.grade(PRESETS["disk_full"], disk_full_final())
    assert result["grade"] == "right"
    assert result["fix"] == "FIXED" and result["option_results"] == ["FIXED"]


def test_the_part_check_in_code_overrules_the_model(monkeypatch):
    """The model says "where: true", but the diagnosis never names the database: not the right part."""
    fake_judge(monkeypatch, '{"where": true, "what": true, "why": false, "reason": "Close."}')
    final = disk_full_final()
    final["root_cause"] = "The shop's API has a bug in the order endpoint."
    result = grader.grade(PRESETS["disk_full"], final)
    assert result["where"] is False and result["grade"] == "partly"


def test_no_answer_is_wrong_and_an_unreadable_grader_gives_no_grade(monkeypatch):
    assert grader.grade(PRESETS["disk_full"], None)["grade"] == "wrong"
    fake_judge(monkeypatch, "I think it's fine.", "Still no JSON.")
    assert grader.grade(PRESETS["disk_full"], disk_full_final())["grade"] is None

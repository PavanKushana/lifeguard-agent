"""
test_stump.py
Checks Stump the Agent: the hidden truth stays hidden until the human decides, the grade is shown
after, and only visitors' own problems are counted on the scoreboard.

No AI here (the grade is made up). Run all tests with:  python -m pytest
"""

from fastapi.testclient import TestClient

from app import incidents, scoreboard
from app.incidents import decide_incident
from app.main import app, incident_view


def test_the_truth_is_hidden_until_the_decision(ready_incident):
    session = ready_incident("disk_full")
    session.incident.truth = {"title": "Database disk full", **session.fault["truth"]}
    session.incident.grade = {"grade": "right", "reason": "Same cause."}
    before = incident_view(session.incident)
    assert "truth" not in before and "grade" not in before  # the person deciding mustn't see the answer
    decide_incident(session, 1, approved_by="test")
    after = incident_view(session.incident)
    assert after["truth"]["service"] == "db" and after["grade"]["grade"] == "right"


def test_scoreboard_counts_grades():
    for grade in ("right", "right", "partly", "wrong"):
        scoreboard.record(grade, "A visitor's problem")
    assert scoreboard.score() == {"solved": 2, "partly": 1, "total": 4}


def test_only_visitors_own_problems_count(ready_incident, monkeypatch):
    monkeypatch.setattr(incidents, "grade", lambda fault, final: {"grade": "right"})
    preset = ready_incident("disk_full")  # a ready-made problem: graded, but not counted
    incidents.grade_incident(preset, preset.incident)
    custom = ready_incident("cache_crashed")
    custom.fault_name = "custom"  # typed by a visitor
    incidents.grade_incident(custom, custom.incident)
    assert preset.incident.grade == {"grade": "right"}
    assert scoreboard.score()["total"] == 1


def test_a_failed_grading_never_breaks_the_incident(ready_incident, monkeypatch):
    def broken(fault, final):
        raise RuntimeError("grader down")

    monkeypatch.setattr(incidents, "grade", broken)
    session = ready_incident("disk_full")
    incidents.grade_incident(session, session.incident)
    assert session.incident.grade["grade"] is None


def test_stump_endpoint():
    assert TestClient(app).get("/stump").json() == {"solved": 0, "partly": 0, "total": 0}

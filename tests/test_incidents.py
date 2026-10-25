"""
test_incidents.py
Checks incidents.py, the one path the website and Telegram share: deciding on a fix, and the result
in words ("Working again: placing orders.") that both of them show.

No AI here (see ready_incident in conftest.py). Run all tests with:  python -m pytest
"""

import pytest

from app.incidents import IncidentError, decide_incident, outcome, proof_lines


def test_disk_full_fixed_names_what_works_again(ready_incident):
    """The words come from this incident's own recovery logs and checks, not from a fixed sentence."""
    session = ready_incident("disk_full")
    result = decide_incident(session, 1, approved_by="test")
    words = outcome(session.incident)
    assert result["verdict"] == "FIXED"
    assert words["back"] == "Working again: placing orders."
    assert words["kept"] == "Stays fixed: job db-backup is off."
    assert "the main disk (/) is 90% full" in words["watch"]


def test_cache_crash_names_the_shopping_cart(ready_incident):
    session = ready_incident("cache_crashed")
    decide_incident(session, 1, approved_by="test")
    assert outcome(session.incident)["back"] == "Working again: the shopping cart."


def test_half_fix_says_the_cause_is_still_there(ready_incident):
    """Only moving the file: customers are OK, but the backup job will fill the disk again."""
    session = ready_incident("disk_full", keep_steps=1)
    result = decide_incident(session, 1, approved_by="test")
    assert result["verdict"] == "FIXED FOR NOW"
    assert outcome(session.incident)["still"] == "The cause is still there: job db-backup is on."


def test_reject_is_not_fixed_and_has_no_proof(ready_incident):
    session = ready_incident("disk_full")
    result = decide_incident(session, 0, approved_by="test")
    assert result["verdict"] == "NOT FIXED"
    assert session.incident.proof == []
    assert outcome(session.incident)["back"] == ""


def test_proof_lines_put_working_requests_first():
    lines = proof_lines(["db: LOG:  checkpoint complete",
                         'web: 172.18.0.1 - - "GET /products HTTP/1.1" 200 5123',
                         "api: INFO POST /api/orders 201 in 58ms"])
    assert lines[0] == {"text": "web: GET /products answered 200 (success)", "success": True}
    assert lines[1] == {"text": "api: POST /api/orders answered 201 (success)", "success": True}
    assert lines[2] == {"text": "db: LOG:  checkpoint complete", "success": False}


def test_decide_refuses_what_it_cant_do(ready_incident):
    """Each impossible request gets its own status and sentence, from the web and Telegram alike."""
    session = ready_incident("disk_full")
    with pytest.raises(IncidentError) as error:
        decide_incident(session, 5, approved_by="test")
    assert error.value.status == 400
    decide_incident(session, 1, approved_by="test")
    with pytest.raises(IncidentError) as error:
        decide_incident(session, 1, approved_by="test")  # a second decision, e.g. phone and website
    assert error.value.status == 409
    session.incident = None
    with pytest.raises(IncidentError) as error:
        decide_incident(session, 1, approved_by="test")
    assert error.value.status == 409

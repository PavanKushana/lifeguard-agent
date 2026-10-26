"""
conftest.py
Shared test helpers (pytest finds this file by itself and hands its fixtures to every test file).

no_real_telegram runs around every test by itself: tests never send real Telegram messages, even
with a bot token and OWNER_CHAT_ID in .env. no_real_models does the same for Token Factory.

ready_incident builds a broken server with a finished agent run, without any AI: the "agent's answer"
is the fix our own code finds (validator.find_fix), so deciding on it gives a real verdict, real
recovery logs and a real postmortem.
"""

import pytest

from app import llm, scoreboard, telegram_bot
from app.faults import PRESETS, apply_fault
from app.llm import Usage
from app.sessions import Incident, Session, get_or_create
from app.validator import find_fix


@pytest.fixture(autouse=True)
def no_real_telegram(monkeypatch):
    """Every Telegram call does nothing during tests, and no note goes to an owner chat.

    25 Oct: the tests read the real bot token and OWNER_CHAT_ID from .env, so each run sent 5 real
    "Someone used the demo" messages to Pavan's phone (one per decision in test_incidents.py).
    Background work runs at once, so nothing is still sending after a test, when the real functions
    are back. Tests that check messages put test_telegram.py's `sent` recorder on top of this.
    """
    monkeypatch.setattr(telegram_bot, "OWNER_CHAT_ID", None)
    monkeypatch.setattr(telegram_bot, "call", lambda method, **params: None)
    monkeypatch.setattr(telegram_bot, "send_document", lambda chat_id, filename, content, caption: None)
    monkeypatch.setattr(telegram_bot, "in_background", lambda function, *args: function(*args))


@pytest.fixture(autouse=True)
def no_real_models(monkeypatch, tmp_path):
    """No test ever calls Token Factory (it costs money and needs the internet): a test that tries fails loudly.

    Tests that need a model answer replace ask() with a fake. The Stump the Agent scoreboard writes to a
    temporary file, so tests never touch the real one.
    """
    def refuse(options):
        raise RuntimeError(f"a test tried to call Token Factory ({options.get('model')}); use a fake ask()")

    monkeypatch.setattr(llm, "create_with_patience", refuse)
    monkeypatch.setattr(scoreboard, "DB_PATH", tmp_path / "scoreboard.sqlite")


@pytest.fixture
def ready_incident():
    """make(preset, steps=None, keep_steps=None) -> a Session whose incident waits for a decision.

    keep_steps=1 keeps only the first step of the found fix (a half fix: users OK, cause still there).
    The session is registered like a real visitor's, so the Telegram code can find it.
    """
    def make(preset: str = "disk_full", keep_steps: int | None = None) -> Session:
        session = get_or_create(None)
        session.fault, session.fault_name = PRESETS[preset], preset
        apply_fault(session.server, session.fault)
        steps = find_fix(session.fault, session.server)[0]
        if keep_steps is not None:
            steps = steps[:keep_steps]
        final = {"root_cause": "The backup job filled the database disk.", "confidence": "high",
                 "why_recommended": "", "sources": [], "evidence": [], "blocked": [],
                 "options": [{"title": "Free space and stop the job", "recommended": True, "steps": steps,
                              "pros": [], "cons": [], "undo": "Move the file back", "reversible": True,
                              "risk": "low"}]}
        incident = Incident(alert=session.fault["alert"])
        incident.result = {"final": final, "usage": Usage(), "triage": {}, "report": "", "evidence": []}
        incident.state = "done"
        session.incident = incident
        return session
    return make

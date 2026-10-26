"""
test_memory.py
Checks the agent's memory of past incidents (memory.py): it finds the past incident that matches an
error or a symptom, says so when nothing is similar, can be switched off (for the evaluation), and
learns from a fix that worked on the demo. An alert seen before is shown before the investigation
starts, but only when the match is close.

No AI here. Run all tests with:  python -m pytest
"""

from types import SimpleNamespace

import app.agent as agent
from app import memory, tools
from app.faults import PRESETS
from app.incidents import decide_incident
from app.llm import Usage
from app.simulator import Server


def test_an_error_message_finds_its_past_incident():
    matches = memory.search("ModuleNotFoundError: No module named 'pydantic_settings'")
    assert matches and "pydantic_settings" in matches[0][1]["root_cause"]


def test_a_full_disk_finds_the_backup_incident():
    text = memory.search_text("could not extend file: No space left on device")
    assert text.startswith("Similar past incidents") and "backup" in text.lower()


def test_nothing_similar_says_so():
    assert memory.search_text("the coffee machine is out of beans").startswith("No similar past incidents")


def test_memory_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(memory, "ENABLED", False)
    answer = tools.run_tool(Server(), "search_past_incidents", '{"query": "No space left on device"}')
    assert "switched off" in answer


def test_a_fix_that_worked_is_remembered(ready_incident):
    session = ready_incident("cache_crashed")
    before = len(memory._records)
    decide_incident(session, 1, approved_by="test")
    assert len(memory._records) == before + 1
    assert memory._records[-1]["alert"] == session.fault["alert"] and memory._records[-1]["source"] == "demo"


def test_an_alert_seen_before_is_recalled_before_the_investigation(monkeypatch):
    """The bad deploy happened on 22 Oct: our code shows it at once, and Super gets it in its first message."""
    first_messages, steps = [], []

    def fake_ask(job, messages, usage, **options):
        """Stands in for Super: keeps its first message, then reports at once (no tools, no AI)."""
        first_messages.append(messages[1]["content"])
        return SimpleNamespace(content="ROOT CAUSE: test", tool_calls=None)

    monkeypatch.setattr(agent, "ask", fake_ask)
    agent.investigate(Server(), PRESETS["bad_deploy"]["alert"], {}, Usage(), lambda kind, text: steps.append(kind))
    assert steps[0] == "memory"
    assert "pydantic_settings" in first_messages[0] and "Checked in our memory" in first_messages[0]


def test_only_a_close_match_is_recalled():
    """A bad nginx deploy shares only the symptom ("500 Internal Server Error") with a past full disk: not shown."""
    assert memory.recall("Users see '500 Internal Server Error' when visiting the shop homepage.") is None
    assert memory.recall("the coffee machine is out of beans") is None


def test_versions_and_names_stay_whole():
    found = memory.words("api 2.4.0: pydantic_settings, No space left on device")
    assert {"2.4.0", "pydantic_settings", "space", "device"} <= found

"""
test_final_call.py
Checks what happens around Ultra's final call, with a fake Ultra instead of the real one.

The fake gives fixed answers, so the test is fast, free and the same every time. What's tested
is OUR code: when safety blocks every option, Ultra is told why and asked once for safe ones.
"""

import json
from types import SimpleNamespace

import app.agent as agent
from app.faults import PRESETS, apply_fault
from app.llm import Usage
from app.simulator import Server

DOWNGRADE_CACHE = {"title": "Rollback cache to Redis 7.2",
                   "steps": [{"type": "rollback", "container": "cache", "to_image": "redis:7.2"}]}
RESTART_CACHE = {"title": "Restart the cache", "steps": [{"type": "restart", "container": "cache"}]}


def final_call_json(*options) -> str:
    """A complete final call as Ultra would write it, recommending the first option."""
    return json.dumps({"root_cause": "Redis crashed.", "options": list(options), "recommended": 1, "sources": []})


def run_final_call(monkeypatch, replies: list[str]) -> tuple[dict, list[str]]:
    """Run final_call on cache_crashed with a fake Ultra that gives these replies in order.

    Returns the final call and every message our code sent to Ultra after the first one.
    """
    answers = iter(replies)
    sent_back = []

    def fake_ask(job, messages, usage, **options):
        """Stands in for llm.ask: no network, just the next prepared reply."""
        usage.last_finish = "stop"
        if len(messages) > 2:
            sent_back.append(messages[-1]["content"])
        return SimpleNamespace(content=next(answers))

    monkeypatch.setattr(agent, "ask", fake_ask)
    server = Server()
    apply_fault(server, PRESETS["cache_crashed"])
    result = agent.final_call(server, "alert", "report", [], Usage(), lambda kind, text: None)
    return result, sent_back


def test_safe_options_need_no_second_try(monkeypatch):
    """A final call with a safe option goes straight through: one call, nothing sent back."""
    result, sent_back = run_final_call(monkeypatch, [final_call_json(RESTART_CACHE)])
    assert [option["title"] for option in result["options"]] == ["Restart the cache"]
    assert sent_back == []


def test_when_every_option_is_blocked_ultra_hears_why(monkeypatch):
    """Only a blocked downgrade first: Ultra must get the reason, and its safe second answer is used."""
    result, sent_back = run_final_call(monkeypatch, [final_call_json(DOWNGRADE_CACHE),
                                                     final_call_json(RESTART_CACHE)])
    assert len(sent_back) == 1
    assert "may not be able to read them" in sent_back[0]
    assert [option["title"] for option in result["options"]] == ["Restart the cache"]
    # The blocked downgrade is kept, so the postmortem can show the safety check working.
    assert [item["title"] for item in result["blocked"]] == ["Rollback cache to Redis 7.2"]


def test_a_second_unsafe_answer_still_gets_blocked(monkeypatch):
    """Asking again never lets an unsafe option through: safety checks the second answer too."""
    result, sent_back = run_final_call(monkeypatch, [final_call_json(DOWNGRADE_CACHE),
                                                     final_call_json(DOWNGRADE_CACHE)])
    assert result["options"] == []
    assert len(result["blocked"]) == 2

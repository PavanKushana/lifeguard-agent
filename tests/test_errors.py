"""
test_errors.py
Checks that an outside service failing ends in a clear message, never a crash (Day 11):
Tavily down, Token Factory busy, Token Factory too slow, and that one request can't hang for long.

No AI and no internet: the failures are made up.
Run all tests with:  python -m pytest
"""

import httpx
from openai import APIStatusError, APITimeoutError

from app import incidents, llm, web_search
from app.faults import PRESETS, apply_fault
from app.sessions import Incident, get_or_create

TOKEN_FACTORY = "https://api.tokenfactory.nebius.com/v1/chat/completions"


def test_tavily_down_gives_the_agent_a_message(monkeypatch):
    """The agent reads the message and carries on with the server tools."""
    class Down:
        def search(self, **options):
            raise ConnectionError("Tavily is down")

    monkeypatch.setattr(web_search, "get_client", lambda: Down())
    assert web_search.search_web("No space left on device").startswith("Error: web search failed (ConnectionError)")


def test_token_factory_busy_ends_the_incident_with_a_clear_message(monkeypatch):
    request = httpx.Request("POST", TOKEN_FACTORY)
    busy = APIStatusError("busy", response=httpx.Response(503, request=request), body=None)

    def fail(server, alert, on_step):
        raise busy

    monkeypatch.setattr(incidents, "handle_incident", fail)
    session = get_or_create(None)
    session.fault, session.fault_name = PRESETS["disk_full"], "disk_full"
    apply_fault(session.server, session.fault)
    session.incident = incident = Incident(alert=session.fault["alert"])
    incidents.run_agent(session, incident)
    assert incident.state == "failed" and "busy" in incident.error


def test_too_slow_has_its_own_message():
    error = APITimeoutError(request=httpx.Request("POST", TOKEN_FACTORY))
    assert "took too long" in llm.describe_error(error)


def test_a_request_cannot_hang_for_minutes(monkeypatch):
    """The library would wait 10 minutes by default; ours gives up after 2 (connecting: 10 s)."""
    monkeypatch.setenv("NEBIUS_API_KEY", "test-key")
    monkeypatch.setattr(llm, "_client", None)
    client = llm.get_client()
    assert client.timeout.read == 120.0 and client.timeout.connect == 10.0

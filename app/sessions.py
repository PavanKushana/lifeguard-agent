"""
sessions.py
One fake server per visitor, so two judges on the demo at the same time never break each other's server.

How a visitor is recognised: on their first request the API gives the browser a random id in a
cookie, and the browser sends that cookie back with every request after. The id points to a
Session here, which holds that visitor's own Server and the incident running on it.

Everything is kept in memory only. Restarting the API forgets every session, which is fine for a
demo: a visitor just gets a fresh healthy server. Old sessions are cleaned up so memory can't fill up.
"""

import secrets
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.simulator import Server

# The cookie that carries the visitor's session id.
COOKIE_NAME = "lifeguard_session"
# A session nobody used for this long is deleted (a judge who closed the tab).
IDLE_LIMIT = timedelta(hours=2)
# Hard ceiling, in case many visitors come at once: the least recently used ones go first.
MAX_SESSIONS = 200


@dataclass
class Incident:
    """One run of the agent on a broken server: its live timeline and, at the end, its results.

    The agent runs in the background and adds each step to `steps` as it happens, so the
    page can show them while the agent is still working.
    """

    alert: str
    started: datetime = field(default_factory=datetime.now)
    # A short random name, so a Telegram button made for this incident can't act on a later one.
    id: str = field(default_factory=lambda: secrets.token_hex(4))
    # "running" while the agent works, then "done" (results are in) or "failed" (an error stopped it).
    state: str = "running"
    # Each step is {"kind": "triage" / "tool_call" / ..., "text": ..., "at": "10:47:34"}, oldest first.
    steps: list[dict] = field(default_factory=list)
    # When each kind of step FIRST happened ("report", "final_call", "verdict", ...), for the postmortem's timeline.
    times: dict[str, datetime] = field(default_factory=dict)
    # What handle_incident() returned: triage, report, final call, evidence, usage. None until done.
    result: dict | None = None
    error: str | None = None
    # The human's decision (from approval.approve_option), what the fix did, and the verdict. None until decided.
    decision: dict | None = None
    done: list[str] = field(default_factory=list)
    verification: dict | None = None
    # After a fix that worked: the new log lines that show it, e.g. "api: INFO POST /api/orders 201 in 58ms".
    proof: list[str] = field(default_factory=list)

    def add_step(self, kind: str, text: str) -> None:
        """Record one step of the agent. Passed to the agent as its on_step, like print_step in the terminal."""
        now = datetime.now()
        self.times.setdefault(kind, now)  # setdefault: a later step of the same kind keeps the first time
        # "at" is the server's clock for the terminal; "time" carries the time zone, so the web page
        # can show it in the visitor's own time (the live server runs on UTC).
        self.steps.append({"kind": kind, "text": text, "at": f"{now:%H:%M:%S}",
                           "time": now.astimezone().isoformat(timespec="seconds")})


@dataclass
class FaultBuild:
    """Ultra building a custom or random fault in the background (it takes 10 seconds to 2 minutes).

    Only short facts are kept for the page. The fault itself goes to the Session when it's ready,
    and is never sent to the browser, so the person approving the fix doesn't know the answer either.
    """

    # "custom" (the visitor's own idea) or "random" (one of ours, kept secret: it would say where to look).
    kind: str
    # The visitor's idea, shown back to them. None for random.
    idea: str | None
    started: datetime = field(default_factory=datetime.now)
    # "building" while Ultra works, then "ready" (the server is broken now) or "failed".
    state: str = "building"
    message: str = "Building your fault, this can take a minute..."
    cost: str | None = None
    # A short update while it's still building, e.g. that a different random problem is being tried.
    note: str | None = None


@dataclass
class Session:
    """One visitor: their own server, and the fault that broke it (None while it's healthy)."""

    id: str
    server: Server = field(default_factory=Server)
    created: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    # The whole fault, hidden truth included. Only the alert is ever sent to the browser.
    fault: dict | None = None
    # Short name for saved files: the preset id, "custom" or "random".
    fault_name: str | None = None
    # A custom or random fault being built. None for presets, which are ready at once.
    build: FaultBuild | None = None
    # The agent's run on this fault. None until the visitor starts the investigation.
    incident: Incident | None = None
    # Telegram (Day 10): the chat that gets this visitor's alerts and may approve their fixes, and a
    # one-time code to link it (random, used once, expires). A reset keeps the link.
    telegram_chat_id: int | None = None
    telegram_code: str | None = None
    telegram_code_until: datetime | None = None
    # The visitor's address from their last request: an investigation that starts by itself
    # (Telegram linked) still counts against that visitor's spending limit.
    last_ip: str = "unknown"

    def reset(self) -> None:
        """Throw the broken server away and start again with a healthy one."""
        self.server = Server()
        self.fault = None
        self.fault_name = None
        # Same idea for a fault still being built: it finishes on its own object and is thrown away.
        self.build = None
        # An agent still running keeps writing to its own Incident object, which nobody
        # looks at any more, so it can't mix old steps into the new server's timeline.
        self.incident = None


# All sessions by id. The lock is there because FastAPI runs requests in several threads at once,
# and two threads changing the same dict at the same moment can lose an entry.
_sessions: dict[str, Session] = {}
_lock = threading.Lock()


def _clean_up(now: datetime) -> None:
    """Delete idle sessions, then the least recently used ones if there are still too many. Call with the lock held."""
    for session_id in [sid for sid, s in _sessions.items() if now - s.last_used > IDLE_LIMIT]:
        del _sessions[session_id]
    while len(_sessions) >= MAX_SESSIONS:
        oldest = min(_sessions.values(), key=lambda s: s.last_used)
        del _sessions[oldest.id]


def find_all(match) -> list[Session]:
    """Every session for which match(session) is true, e.g. the ones linked to a Telegram chat."""
    with _lock:
        return [session for session in _sessions.values() if match(session)]


def find(match) -> Session | None:
    """The first session for which match(session) is true, or None."""
    found = find_all(match)
    return found[0] if found else None


def get_or_create(session_id: str | None) -> Session:
    """Return the visitor's session, or a new one with a healthy server if the id is missing, unknown or expired."""
    now = datetime.now()
    with _lock:
        session = _sessions.get(session_id) if session_id else None
        if session is None:
            _clean_up(now)
            # 32 random characters: impossible to guess, so nobody can open another visitor's server.
            session = Session(id=secrets.token_urlsafe(24))
            _sessions[session.id] = session
        session.last_used = now
        return session

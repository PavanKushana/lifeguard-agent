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

    def reset(self) -> None:
        """Throw the broken server away and start again with a healthy one."""
        self.server = Server()
        self.fault = None
        self.fault_name = None


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

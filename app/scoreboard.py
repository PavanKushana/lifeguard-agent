"""
scoreboard.py
Stump the Agent's scoreboard: of the problems visitors typed in their own words, how many did the
agent get right? Shown on the page as "the agent found the cause in X of Y".

Only custom faults count (the visitor's own idea), not the ready-made ones or "Pick one for me".
Kept in a small SQLite file (one file, no database server). On the free host the disk starts empty
again after each update of the demo, so the count starts over then; the page says "since the last
update". Nothing personal is stored: the time, the fault's title (written by Ultra) and the grade.
"""

import os
import sqlite3
import tempfile
import threading
from datetime import datetime
from pathlib import Path

# In the temp folder: the server runs as a normal user in Docker, and that's a place it may write to.
DB_PATH = Path(os.environ.get("SCOREBOARD_PATH", Path(tempfile.gettempdir()) / "lifeguard-scoreboard.sqlite"))
# Requests run in several threads at once; SQLite is happiest with one writer at a time.
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    """Open the file (made on first use) and make sure the table exists."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    connection.execute("CREATE TABLE IF NOT EXISTS stumps (at TEXT, title TEXT, grade TEXT)")
    return connection


def record(grade: str, title: str) -> None:
    """Count one visitor's own problem with the agent's grade (right, partly or wrong)."""
    with _lock:
        connection = _connect()
        try:
            with connection:  # commits at the end of the block
                connection.execute("INSERT INTO stumps VALUES (?, ?, ?)",
                                   (datetime.now().isoformat(timespec="seconds"), title[:120], grade))
        finally:
            connection.close()


def score() -> dict:
    """{"solved": right answers, "partly": partly right, "total": visitors' problems graded so far}."""
    with _lock:
        connection = _connect()
        try:
            rows = dict(connection.execute("SELECT grade, COUNT(*) FROM stumps GROUP BY grade").fetchall())
        finally:
            connection.close()
    return {"solved": rows.get("right", 0), "partly": rows.get("partly", 0), "total": sum(rows.values())}

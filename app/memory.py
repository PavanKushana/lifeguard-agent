"""
memory.py
The agent's memory of past incidents: what happened before, what the cause was, and which fix worked.

Real on-call engineers first ask "have we seen this before?". Two ways here:
- recall(): the moment an alert comes in, our code looks it up and shows close matches (no model decides);
- the search_past_incidents tool: the investigator (Super) searches with the error it found, any time.
A match is a hint, never proof: the agent must still check it on this server, because the same
symptom can have a different cause this time.

Where the memories come from:
- app/past_incidents.json: incidents from our own test runs (21-23 Oct), made from their postmortems by
  scripts/build_memory.py, so the demo already remembers something on its first visit;
- every incident decided on the demo with a fix that worked (remember()). Those are kept in the server's
  memory only, so an update of the demo starts again from the file.

How a match is found: plain word matching, no AI. The question and every past incident become sets of
words (lowercase, without common words like "the" or "error"), and words shared with the question count;
rare words count more than common ones ("pydantic_settings" says more than "container"). Simple, fast,
free and easy to explain. An embedding model could replace it later without changing the tool.
"""

import json
import math
import re
import threading
from pathlib import Path

SEED_FILE = Path(__file__).resolve().parent / "past_incidents.json"
# A past incident must share at least this much of the question's (weighted) words to count as similar.
MIN_MATCH = 0.3
# Before the investigation starts, our code looks the alert up (recall). That is shown to everyone and
# given to Super unasked, so it needs a much closer match. 26 Oct check with the 20 eval alerts: from 0.75
# on, every match was the same problem; between 0.5 and 0.6 most shared the symptom but not the cause
# (a bad nginx deploy matched "a cron job filled the disk" at 60%, both alerts say "500 Internal Server Error").
RECALL_MATCH = 0.75
MAX_RESULTS = 2
MAX_RECORDS = 100  # the oldest demo incidents are dropped after that, so memory can't grow forever
# Switched off by the evaluation (scripts/run_eval.py), so its numbers measure the agent alone.
ENABLED = True

# Words that appear in almost every incident and say nothing about which one it is.
COMMON = set("""
the and for with that this from was were are has have had not but its into out over then than when
while after before about all any can could would should will just also only one two new old more most
very some such been being which what who how why where there their they them our your you she him her
error errors errored fail failed fails failing failure container containers server shop customers customer
because due caused causing cause root issue problem page pages users user see shows show showing
""".split())

_lock = threading.Lock()


def words(text: str) -> set[str]:
    """The meaningful words of a text: 'No space left on device!' -> {'space', 'left', 'device'}.

    Version numbers stay whole ('2.4.0'), and so do names with underscores ('pydantic_settings').
    """
    found = re.findall(r"[a-z][a-z0-9_]+|\d+(?:\.\d+)+", text.lower())
    return {word for word in found if len(word) > 2 and word not in COMMON}


def record_text(record: dict) -> str:
    """Everything about a past incident that a search can match: what users saw, the cause, its evidence, the fix."""
    return " ".join([record.get("alert", ""), record.get("root_cause", ""), " ".join(record.get("evidence", [])),
                     record.get("fix", "")])


def _load() -> list[dict]:
    """The starting memory from app/past_incidents.json (empty if the file is missing)."""
    try:
        return json.loads(SEED_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


_records: list[dict] = _load()


def search(query: str, min_match: float = MIN_MATCH) -> list[tuple[float, dict]]:
    """The past incidents most like the query, best first, as (how much matches 0..1, record)."""
    with _lock:
        records = list(_records)
    question = words(query)
    if not question or not records:
        return []
    texts = [words(record_text(record)) for record in records]
    # Rare words count more: a word in 1 of 12 incidents says more than one in 10 of 12.
    weight = {word: math.log((len(texts) + 1) / (1 + sum(word in text for text in texts))) + 1 for word in question}
    total = sum(weight.values())
    scored = [(sum(weight[word] for word in question & text) / total, record) for text, record in zip(texts, records)]
    return sorted([pair for pair in scored if pair[0] >= min_match], key=lambda pair: pair[0], reverse=True)[:MAX_RESULTS]


def search_text(query: str, min_match: float = MIN_MATCH) -> str:
    """The search_past_incidents tool's answer for the agent, in plain text."""
    if not ENABLED:
        return "Memory of past incidents is switched off. Continue with the server tools."
    matches = search(query, min_match)
    if not matches:
        return f"No similar past incidents found for: {query}"
    lines = ["Similar past incidents (only a hint: check it on this server, the cause can be different this time):"]
    for number, (score, record) in enumerate(matches, start=1):
        lines.append(f"{number}. {record['when']}: \"{record['alert']}\" (match {score:.0%})")
        lines.append(f"   Cause then: {record['root_cause']}")
        lines.append(f"   Fix that worked then: {record['fix']} ({record['result']})")
        if record.get("follow_up"):
            lines.append(f"   Follow-up then: {record['follow_up']}")
    return "\n".join(lines)


def recall(alert: str) -> str | None:
    """Has this shop seen this alert before? The memory's answer if a past incident matches closely, else None.

    Our code asks this the moment an alert comes in, like an on-call tool that shows similar past
    incidents next to a new alert. No model decides whether to look.
    """
    if not ENABLED or not search(alert, RECALL_MATCH):
        return None
    return search_text(alert, RECALL_MATCH)


def remember(record: dict) -> None:
    """Add an incident whose fix worked, so the next similar one can be found (kept until the server restarts)."""
    with _lock:
        _records.append(record)
        del _records[:-MAX_RECORDS]

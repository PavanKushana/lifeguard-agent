"""
limits.py
The spending limit for the public demo: how many AI runs one visitor, and everybody together,
may start. Anyone on the internet can open the page, so without this one person (or a bot)
could use up the Token Factory and Tavily credit in an afternoon.

An "AI run" is anything that calls the models: an investigation (Nano, Super, Ultra, Tavily)
or building a custom or random fault (guard + Ultra). Presets, deciding and the postmortem
call no model, so they don't count.

Per visitor the limit is about fairness (an address can be faked); the total limits protect
the credit, and nobody can get around those. The counts live in memory, like the sessions:
a restart starts them from zero, which is fine for a demo.
The numbers can be changed on the host without touching the code (environment variables, see
LIMITS), and SPENDING_LIMITS=off in .env switches the limit off for testing on a laptop.
"""

import os
import threading
import time
from collections import deque

from dotenv import load_dotenv

HOUR = 60 * 60
DAY = 24 * HOUR

# Read .env on a laptop (on the host the settings are real environment variables already).
load_dotenv()


def setting(name: str, default: int) -> int:
    """A number from the environment (e.g. set on Render), or the default."""
    return int(os.environ.get(name, default))


# (who, window in seconds, most runs allowed in that window). "visitor" = one IP address,
# "everyone" = all visitors together. An investigation costs about $0.03 and a fault build
# $0.03-0.05 (Token Factory prices, 24 Oct), so even a full day at the limit stays under $5.
LIMITS = [
    ("visitor", HOUR, setting("LIMIT_VISITOR_PER_HOUR", 8)),
    ("visitor", DAY, setting("LIMIT_VISITOR_PER_DAY", 25)),
    ("everyone", HOUR, setting("LIMIT_TOTAL_PER_HOUR", 40)),
    ("everyone", DAY, setting("LIMIT_TOTAL_PER_DAY", 100)),
]


def wait_text(seconds: float) -> str:
    """How long until the next run is allowed, in words: "in 1 minute", "in 12 minutes", "in 3 hours"."""
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"in {minutes} minute{'' if minutes == 1 else 's'}"
    return f"in {round(minutes / 60)} hours"


def refusal(who: str, seconds: int, most: int, wait: float) -> str:
    """The sentence the visitor reads when a limit is reached: which limit, and when they can go on."""
    per = "hour" if seconds == HOUR else "day"
    if who == "visitor":
        return (f"To keep this demo free for everyone, each visitor can start {most} AI runs per {per} "
                f"(investigations and your own or picked problems). You can start the next one {wait_text(wait)}.")
    return (f"The demo is very busy: everyone together has started {most} AI runs in the last {per}, "
            f"the most it allows to protect its free AI credit. Please try again {wait_text(wait)}.")


class SpendingLimit:
    """Counts AI runs in sliding time windows and refuses one that would go over a limit."""

    def __init__(self, limits: list[tuple[str, int, int]], clock=time.monotonic):
        self.limits = limits
        self.clock = clock  # a function that returns seconds; the tests pass a fake one
        self.everyone: deque[float] = deque()         # times of all runs, oldest first
        self.visitors: dict[str, deque[float]] = {}   # the same, per visitor address
        # FastAPI handles requests in several threads at once: two runs counted at the same
        # moment must not both slip through the last free place.
        self.lock = threading.Lock()

    def forget_old(self, now: float) -> None:
        """Drop runs older than the longest window: they can't count against any limit any more."""
        longest = max(seconds for _, seconds, _ in self.limits)
        for times in [self.everyone, *self.visitors.values()]:
            while times and now - times[0] >= longest:
                times.popleft()
        # Visitors with nothing left are removed, so the list can't grow forever.
        self.visitors = {address: times for address, times in self.visitors.items() if times}

    def try_start(self, visitor: str) -> str | None:
        """Count one AI run for this visitor, or return why it's refused (and count nothing)."""
        now = self.clock()
        with self.lock:
            self.forget_old(now)
            for who, seconds, most in self.limits:
                times = self.everyone if who == "everyone" else self.visitors.get(visitor, deque())
                recent = [moment for moment in times if now - moment < seconds]
                if len(recent) >= most:
                    # The oldest run in the window leaves it first: that's when one place frees up.
                    return refusal(who, seconds, most, wait=seconds - (now - recent[0]))
            self.everyone.append(now)
            self.visitors.setdefault(visitor, deque()).append(now)
            return None


# The one limit the web API uses. Off only when SPENDING_LIMITS=off (testing on a laptop).
spending_limit = SpendingLimit(LIMITS)
LIMITS_ON = os.environ.get("SPENDING_LIMITS", "on").strip().lower() != "off"


def try_start_ai_run(visitor: str) -> str | None:
    """Count one AI run for this visitor, or return the reason it's refused."""
    return spending_limit.try_start(visitor) if LIMITS_ON else None

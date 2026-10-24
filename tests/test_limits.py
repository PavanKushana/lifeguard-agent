"""
test_limits.py
Checks the spending limit in limits.py: a visitor or everyone together can't start more AI runs
than allowed, a refused run isn't counted, and old runs stop counting after their window.

No AI and no waiting: each test gives the limit a fake clock and moves it forward by hand.
Run all tests from the project folder with:  python -m pytest
"""

from app.limits import DAY, HOUR, SpendingLimit, wait_text


class FakeClock:
    """A clock the test moves forward by hand, in seconds."""

    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def make_limit(per_visitor_hour=2, total_hour=3, per_visitor_day=10, total_day=20):
    """A limit with small numbers, so a few calls reach it."""
    clock = FakeClock()
    limits = [("visitor", HOUR, per_visitor_hour), ("visitor", DAY, per_visitor_day),
              ("everyone", HOUR, total_hour), ("everyone", DAY, total_day)]
    return SpendingLimit(limits, clock=clock), clock


def test_visitor_limit_per_hour():
    """Two runs are fine, the third in the same hour is refused with the reason and the wait."""
    limit, _ = make_limit()
    assert limit.try_start("1.1.1.1") is None
    assert limit.try_start("1.1.1.1") is None
    reason = limit.try_start("1.1.1.1")
    assert "each visitor can start 2 AI runs per hour" in reason
    assert "in 60 minutes" in reason


def test_other_visitors_are_not_blocked_by_one():
    """One visitor using up their runs doesn't stop someone else."""
    limit, _ = make_limit()
    limit.try_start("1.1.1.1")
    limit.try_start("1.1.1.1")
    assert limit.try_start("2.2.2.2") is None


def test_total_limit_for_everyone():
    """Three different visitors use up the total of 3; the fourth gets the "very busy" message."""
    limit, _ = make_limit()
    for address in ("1.1.1.1", "2.2.2.2", "3.3.3.3"):
        assert limit.try_start(address) is None
    assert "The demo is very busy" in limit.try_start("4.4.4.4")


def test_runs_stop_counting_after_the_hour():
    """After an hour the old runs leave the window and the visitor can start again."""
    limit, clock = make_limit()
    limit.try_start("1.1.1.1")
    limit.try_start("1.1.1.1")
    clock.now += HOUR
    assert limit.try_start("1.1.1.1") is None


def test_a_refused_run_is_not_counted():
    """Being refused doesn't use a place: the wait stays the same, it doesn't grow."""
    limit, clock = make_limit()
    limit.try_start("1.1.1.1")
    clock.now += 30 * 60
    limit.try_start("1.1.1.1")
    clock.now += 10 * 60
    assert "in 20 minutes" in limit.try_start("1.1.1.1")  # the first run leaves the hour 20 min later
    clock.now += 20 * 60
    assert limit.try_start("1.1.1.1") is None


def test_day_limit():
    """Spread over many hours, the day limit still stops a visitor at its number."""
    limit, clock = make_limit(per_visitor_hour=100, total_hour=100, per_visitor_day=3, total_day=100)
    for _ in range(3):
        assert limit.try_start("1.1.1.1") is None
        clock.now += 2 * HOUR
    assert "per day" in limit.try_start("1.1.1.1")


def test_wait_in_words():
    assert wait_text(30) == "in 1 minute"
    assert wait_text(12 * 60) == "in 12 minutes"
    assert wait_text(5 * HOUR) == "in 5 hours"

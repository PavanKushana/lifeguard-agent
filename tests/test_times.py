"""
test_times.py
Checks the time zone label in postmortems. The live server runs on UTC; without the label a
visitor reads its times as their own, and they look hours off (seen on 24 Oct, the first day online).

No AI here. Run all tests from the project folder with:  python -m pytest
"""

from datetime import datetime, timedelta, timezone

from app.postmortem import zone_name


def test_utc_server_says_utc():
    assert zone_name(datetime(2026, 10, 24, 19, 9, tzinfo=timezone.utc)) == "UTC"


def test_other_zones_show_their_offset():
    india = timezone(timedelta(hours=5, minutes=30))
    assert zone_name(datetime(2026, 10, 24, 19, 9, tzinfo=india)) == "UTC+05:30"
    assert zone_name(datetime(2026, 10, 24, 19, 9, tzinfo=timezone(timedelta(hours=-4)))) == "UTC-04:00"


def test_time_without_zone_uses_the_servers_own_zone():
    """The app's times have no zone attached (datetime.now()): they're the server's clock."""
    assert zone_name(datetime.now()).startswith("UTC")

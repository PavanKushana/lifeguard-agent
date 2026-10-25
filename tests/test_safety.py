"""
test_safety.py
Checks the guardrail in safety.py: dangerous fixes are blocked, normal fixes pass.

No AI is used here. Each test builds a fake server, asks check_action() about one fix step,
and checks the answer: None means "safe", a sentence means "blocked, and here's why".
Run all tests from the project folder with:  python -m pytest
"""

from app.faults import PRESETS, apply_fault
from app.safety import check_action, check_steps
from app.simulator import Server


def broken(preset: str) -> Server:
    """A fresh server, broken by one of the preset faults."""
    server = Server()
    apply_fault(server, PRESETS[preset])
    return server


def test_restart_is_allowed():
    """The simplest fix, restarting a container, must always pass."""
    assert check_action(Server(), {"type": "restart", "container": "cache"}) is None


def test_unknown_action_is_blocked():
    """Only actions on the menu can run; anything else is blocked."""
    assert "not on the action menu" in check_action(Server(), {"type": "reboot_server"})


def test_moving_database_data_is_blocked():
    """Moving the database's own data folder can destroy data (seen for real on 22 Oct)."""
    step = {"type": "move_files", "from_path": "/var/lib/postgresql/data", "to_mount": "/"}
    assert "database's own data" in check_action(Server(), step)


def test_move_that_does_not_fit_is_blocked():
    """Moving 18.4 GB of backups onto a disk with less free space would fill that disk too."""
    server = broken("disk_full")
    # Both dumps together (31.6 GB) don't fit on / (18.8 GB free); only one at a time does.
    step = {"type": "move_files", "from_path": "/var/lib/postgresql/backups", "to_mount": "/"}
    assert "has only" in check_action(server, step)


def test_forbidden_command_is_blocked():
    """Words that delete data (like rm -rf) block the whole option, wherever they appear."""
    steps = [{"type": "restart", "container": "db", "note": "then rm -rf /var/lib/postgresql/data"}]
    assert "forbidden" in check_steps(Server(), "Clean up", steps)


def test_rolling_back_cache_after_days_is_blocked():
    """cache_crashed: Redis 7.4 ran for 9 days. Going back to 7.2 can't read 7.4's data files."""
    step = {"type": "rollback", "container": "cache", "to_image": "redis:7.2"}
    assert "may not be able to read them" in check_action(broken("cache_crashed"), step)


def test_rolling_back_a_fresh_cache_upgrade_is_allowed():
    """The same rule must NOT block undoing an upgrade from minutes ago: that's the right fix then."""
    server = Server()
    server.containers["cache"].image = "redis:7.5"
    server.add_change("Upgraded cache from redis 7.4 to 7.5", 30)
    assert check_action(server, {"type": "rollback", "container": "cache", "to_image": "redis:7.4"}) is None


def test_rolling_back_the_api_is_allowed():
    """The api keeps no data, so rolling back a bad deploy is always possible."""
    step = {"type": "rollback", "container": "api", "to_image": "shopapp/api:2.3.1"}
    assert check_action(broken("bad_deploy"), step) is None


def test_a_restart_cant_promise_a_password_or_package_change():
    """25 Oct: "Correct password and restart api" only restarted, so the password stayed wrong (NOT FIXED)."""
    restart_api = [{"type": "restart", "container": "api"}]
    server = broken("wrong_db_password")
    assert "only restart" in check_steps(server, "Correct password and restart api", restart_api)
    assert "only restart" in check_steps(server, "Set DATABASE_PASSWORD and restart api", restart_api)
    assert "only restart" in check_steps(broken("bad_deploy"), "Install pydantic_settings and restart api",
                                         restart_api)


def test_honest_titles_still_pass():
    """A plain restart passes, and so does a rollback: it really brings the old setting back."""
    assert check_steps(broken("cache_crashed"), "Restart cache", [{"type": "restart", "container": "cache"}]) is None
    rollback = [{"type": "rollback", "container": "api", "to_image": "shopapp/api:2.3.1"}]
    assert check_steps(broken("wrong_db_password"), "Roll back api to the version with the old password setting",
                       rollback) is None

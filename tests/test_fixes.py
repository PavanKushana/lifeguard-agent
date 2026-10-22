"""
test_fixes.py
Checks how fixes change the fake server, and how the verdict is decided.

FIXED = users are fine AND the cause is gone. FIXED FOR NOW = users are fine, but it will
come back. NOT FIXED = users still see the problem. And nothing changes without approval.
"""

from app.faults import PRESETS, apply_fault
from app.fixes import apply_fix, verify
from app.simulator import Server


def disk_full_server() -> Server:
    """A fresh server with the backup job filling the database disk."""
    server = Server()
    apply_fault(server, PRESETS["disk_full"])
    return server


def approve(*steps) -> dict:
    """An approved decision with the given steps, like a human said yes."""
    return {"approved": True, "option": {"steps": list(steps)}, "by": "pytest"}


def oldest_backup(server: Server) -> str:
    """The path of the older of the two backup dumps on the database disk."""
    backups = [path for path in server.files if "/backups/" in path]
    return min(backups, key=lambda path: server.files[path].modified)


def test_nothing_changes_without_approval():
    """A decision that wasn't approved must not touch the server."""
    server = disk_full_server()
    rejected = {"approved": False, "option": {"steps": [{"type": "disable_job", "job": "db-backup"}]}}
    assert apply_fix(server, rejected) == []
    assert server.jobs["db-backup"].enabled


def test_freeing_space_without_stopping_the_job_is_only_fixed_for_now():
    """Moving one dump frees space, but the job writes a new one tonight: FIXED FOR NOW."""
    server = disk_full_server()
    apply_fix(server, approve({"type": "move_files", "from_path": oldest_backup(server), "to_mount": "/"}))
    assert verify(server, PRESETS["disk_full"])["verdict"] == "FIXED FOR NOW"


def test_freeing_space_and_stopping_the_job_is_fixed():
    """Both steps together end the problem and its cause: FIXED."""
    server = disk_full_server()
    apply_fix(server, approve({"type": "move_files", "from_path": oldest_backup(server), "to_mount": "/"},
                              {"type": "disable_job", "job": "db-backup"}))
    assert verify(server, PRESETS["disk_full"])["verdict"] == "FIXED"


def test_moving_files_really_moves_the_space():
    """After a move, the database disk has more room and / has less, by the same amount."""
    server = disk_full_server()
    path = oldest_backup(server)
    size = server.files[path].size_gb
    db_before, root_before = server.disks["/var/lib/postgresql"].used_gb, server.disks["/"].used_gb
    apply_fix(server, approve({"type": "move_files", "from_path": path, "to_mount": "/"}))
    assert round(db_before - server.disks["/var/lib/postgresql"].used_gb, 1) == size
    assert round(server.disks["/"].used_gb - root_before, 1) == size


def test_only_disabling_the_job_is_not_fixed():
    """Stopping the job alone leaves the disk full, so users still can't order: NOT FIXED."""
    server = disk_full_server()
    apply_fix(server, approve({"type": "disable_job", "job": "db-backup"}))
    assert verify(server, PRESETS["disk_full"])["verdict"] == "NOT FIXED"

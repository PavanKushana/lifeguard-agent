"""
fixes.py
Carry out an approved fix on the fake server, then check if it really worked.

Two parts:
1. apply_fix(): runs the steps of the option a human approved (approval.py), one by one.
   Each action changes the Server the way the real command would: a rollback changes the
   image, move_files moves the files and their size to the other disk, and so on.
   Every step is also written to the server's recent changes, so there is a record of it.
2. verify(): checks the fault's hidden "fixed_when" rules (faults.py) and gives a verdict:
   FIXED           users are OK and the cause is gone
   FIXED FOR NOW   users are OK, but the cause is still there, so it will come back
   NOT FIXED       users still see the problem
   If users are OK, the server also recovers (errors stop), using the fault's "recovery".

The agent never sees fixed_when. Like the hidden truth, it's only used to grade the result.
"""

from app.faults import apply_changes
from app.simulator import Server, minutes_ago

# Moved files go into this folder on the target disk, so it's easy to see what a fix moved.
MOVED_FOLDER = "moved-files"

# A disk this full is a warning sign: many teams alert at 85%.
NEARLY_FULL = 0.85


def move_files(server: Server, from_path: str, to_mount: str) -> str:
    """Move a file or folder to another disk: the files get a new path and their size changes disks."""
    source = from_path.rstrip("/")
    items = server.items_under(source)
    size = sum(item.size_gb for item in items.values())
    # Keep the moved thing's own name: ".../backups/shop.dump" becomes "/moved-files/shop.dump".
    parent = source.rsplit("/", 1)[0]
    target_root = to_mount.rstrip("/") + "/" + MOVED_FOLDER
    for path, item in items.items():
        del server.files[path]
        server.files[target_root + path[len(parent):]] = item

    from_disk, to_disk = server.disks[server.disk_of(source)], server.disks[to_mount]
    # Rounded, so 50.0 - 15.8 shows as 34.2 and not 34.199999999999996.
    from_disk.used_gb = round(from_disk.used_gb - size, 1)
    to_disk.used_gb = round(to_disk.used_gb + size, 1)
    return f"moved {source} ({size:.1f} GB) to {target_root}/ on {to_mount}"


def run_step(server: Server, step: dict) -> str:
    """Carry out one action from the menu in safety.py and return what was done, in one line."""
    kind = step["type"]
    if kind == "rollback":
        container = server.containers[step["container"]]
        container.image = step["to_image"]
        # A rollback starts a new container from the old image, so it's "Up" from now.
        container.status, container.exit_code = "running", 0
        container.since = minutes_ago(0)
        return f"rolled back {container.name} to {container.image}"
    if kind == "restart":
        container = server.containers[step["container"]]
        container.status, container.exit_code = "running", 0
        container.since = minutes_ago(0)
        return f"restarted {container.name}"
    if kind == "set_memory_limit":
        container = server.containers[step["container"]]
        container.memory_limit_mb = int(step["memory_mb"])
        return f"set the memory limit of {container.name} to {container.memory_limit_mb} MB"
    if kind == "move_files":
        return move_files(server, step["from_path"], step["to_mount"])
    if kind == "disable_job":
        server.jobs[step["job"]].enabled = False
        return f"turned off the job {step['job']}"
    # safety.py only lets menu actions through, so this only happens if the menu and this file disagree.
    raise ValueError(f"no way to run action '{kind}'")


def apply_fix(server: Server, decision: dict) -> list[str]:
    """Run every step of an approved option, in order. Returns one line per step saying what was done.

    Only an approved decision can change the server: no approval, no change.
    """
    if not decision.get("approved"):
        return []
    done = []
    for step in decision["option"]["steps"]:
        line = run_step(server, step)
        # Like a real team's change log: what was done and who approved it.
        server.add_change(f"Lifeguard-Agent fix: {line} (approved by {decision['by']})")
        done.append(line)
    return done


def check(server: Server, rule: dict) -> tuple[bool, str]:
    """Test one check from a fault's fixed_when. Returns (passed, a short sentence saying what was found)."""
    kind = rule["check"]
    if kind == "disk_free_gb":
        disk = server.disks[rule["mount"]]
        free = round(disk.size_gb - disk.used_gb, 1)
        return free >= rule["at_least"], f"{rule['mount']} has {free:.1f} GB free (needs {rule['at_least']:.1f})"
    if kind == "job_enabled":
        job = server.jobs.get(rule["job"])
        enabled = bool(job and job.enabled)
        return enabled == rule["is"], f"job {rule['job']} is {'on' if enabled else 'off'}"
    if kind == "image":
        image = server.containers[rule["container"]].image
        return image == rule["is"], f"{rule['container']} runs {image}"
    if kind == "memory_limit_mb":
        limit = server.containers[rule["container"]].memory_limit_mb
        return limit >= rule["at_least"], (f"{rule['container']} memory limit is {limit} MB "
                                           f"(needs {rule['at_least']})")
    if kind == "container_running":
        status = server.containers[rule["container"]].status
        return status == "running", f"{rule['container']} is {status}"
    raise ValueError(f"unknown check '{kind}'")


def holds(server: Server, group: dict) -> tuple[bool, list[str]]:
    """Test a group of checks: {"all": [...]} needs every check, {"any": [...]} needs one.

    Returns (passed, lines): a heading that says how the checks combine, then one line per
    check with OK or NO, so the human sees why. Without the heading, "FIXED" next to a "NO"
    looks like a contradiction when only one of the checks was needed.
    """
    need_all = "all" in group
    results = [check(server, rule) for rule in group["all" if need_all else "any"]]
    passed = all(ok for ok, _ in results) if need_all else any(ok for ok, _ in results)
    lines = [f"{'OK ' if ok else 'NO '} {text}" for ok, text in results]
    if len(results) > 1:
        lines.insert(0, "(all of these are needed)" if need_all else "(one of these is enough)")
    return passed, lines


def verify(server: Server, fault: dict) -> dict:
    """Check if the fix worked, and let the server recover if users are OK again.

    Returns {"verdict": "FIXED" / "FIXED FOR NOW" / "NOT FIXED", "users_ok": [...], "stays_fixed": [...],
    "warnings": [...]} where the lists are lines for the human to read.
    """
    # A fix can move a problem instead of ending it (moving files fills the other disk),
    # so every disk is checked, not only the one the fault was about.
    warnings = [f"{d.mount} is {d.used_gb / d.size_gb:.0%} full now ({d.size_gb - d.used_gb:.1f} GB free)"
                for d in server.disks.values() if d.used_gb / d.size_gb >= NEARLY_FULL]
    rules = fault["fixed_when"]
    users_ok, users_lines = holds(server, rules["users_ok"])
    stays_fixed, stays_lines = holds(server, rules["stays_fixed"])

    if users_ok:
        # Users are fine again, so the errors stop and the logs show normal traffic.
        apply_changes(server, fault.get("recovery", {}))

    if users_ok and stays_fixed:
        verdict = "FIXED"
    elif users_ok:
        verdict = "FIXED FOR NOW"
    else:
        verdict = "NOT FIXED"
    return {"verdict": verdict, "users_ok": users_lines, "stays_fixed": stays_lines, "warnings": warnings}

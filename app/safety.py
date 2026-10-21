"""
safety.py
The guardrail between the AI and the server: plain Python rules, no AI.

Ultra can only suggest fixes from a fixed menu of actions (ACTIONS below). A fix option has
1-3 steps, each one action from the menu. Before any option is shown to a human,
check_options() tests every step of every option: is it a real action,
on a real container, disk or job, with sensible values, does a move fit on the target disk,
and is there nothing that can destroy data?
Options that fail are blocked, with the reason, so a human can see the guardrail working.

The AI suggests, this file checks, a human approves. Nothing runs without all three.
"""

import re

from app.simulator import Server

# The only things a fix can do, and the details each one needs.
# Deleting things is not on the menu at all: freeing space means moving files, which can be undone.
ACTIONS = {
    "rollback": {
        "params": ["container", "to_image"],
        "meaning": "run an earlier image version of a container, e.g. undo a bad deploy",
    },
    "restart": {
        "params": ["container"],
        "meaning": "restart a container",
    },
    "set_memory_limit": {
        "params": ["container", "memory_mb"],
        "meaning": "change how much memory a container may use",
    },
    "move_files": {
        "params": ["from_path", "to_mount"],
        "meaning": "move a file or folder (exact path from check_folder_sizes) to another disk to free space; "
                   "it must fit into that disk's free space",
    },
    "disable_job": {
        "params": ["job"],
        "meaning": "turn off a scheduled job, by its exact name from list_jobs",
    },
}

# Real fixes often need more than one step (free space AND stop what filled it), but never many.
MAX_STEPS = 3

# Memory limits outside this range make no sense for one container on a small server.
MIN_MEMORY_MB = 128
MAX_MEMORY_MB = 4096

# Folders inside the database disk that hold the data itself. Moving them breaks PostgreSQL.
DATABASE_FOLDERS = ["pg_wal", "base", "global", "pg_xact"]

# Words that must never appear in a fix: they delete data or wipe a database.
FORBIDDEN = [
    r"\brm\s+-[a-z]*r",       # rm -r, rm -rf
    r"\bdrop\s+(table|database)\b",
    r"\btruncate\b",
    r"\bpg_resetwal\b",
    r"\bmkfs\b",              # formats a disk
]

# Fields every option should have for the human to decide. Missing ones are filled in, not blocked.
DISPLAY_DEFAULTS = {"pros": [], "cons": [], "undo": "not given", "reversible": False, "risk": "unknown"}


def find_forbidden(text: str) -> str | None:
    """Return the first forbidden pattern found in the text, or None if it's clean."""
    for pattern in FORBIDDEN:
        if re.search(pattern, text, re.IGNORECASE):
            return pattern
    return None


def touches_protected(server: Server, path: str) -> bool:
    """True if moving `path` would move a protected folder, or something inside one."""
    return any(path == p or path.startswith(p + "/") or p.startswith(path + "/")
               for p in server.protected_paths)


def check_action(server: Server, action: dict) -> str | None:
    """Check one action against the menu and the server. Returns the reason it's unsafe, or None if it's OK."""
    kind = action.get("type")
    if kind not in ACTIONS:
        return f"'{kind}' is not on the action menu"

    missing = [p for p in ACTIONS[kind]["params"] if p not in action]
    if missing:
        return f"missing details: {', '.join(missing)}"

    if "container" in action and action["container"] not in server.containers:
        return f"there is no container called '{action['container']}'"

    if kind == "rollback":
        current = server.containers[action["container"]].image
        # Same image name, different version: api 2.4.0 can go back to api 2.3.1, but never become redis.
        if action["to_image"].split(":")[0] != current.split(":")[0]:
            return f"{action['to_image']} is a different program than {current}"
        if action["to_image"] == current:
            return f"{current} is already running"

    if kind == "set_memory_limit":
        try:
            memory = int(action["memory_mb"])
        except (TypeError, ValueError):
            return f"memory_mb must be a number, got {action['memory_mb']!r}"
        if not MIN_MEMORY_MB <= memory <= MAX_MEMORY_MB:
            return f"{memory} MB is outside the allowed {MIN_MEMORY_MB}-{MAX_MEMORY_MB} MB"
        # All limits together must fit into the machine, or containers fight over memory.
        others = sum(c.memory_limit_mb for c in server.containers.values() if c.name != action["container"])
        if others + memory > server.memory_total_mb:
            return (f"all limits together would be {others + memory} MB, "
                    f"but the server only has {server.memory_total_mb} MB")

    if kind == "move_files":
        source = action["from_path"].rstrip("/")
        from_disk = server.disk_of(source)
        if from_disk is None:
            return f"{source} is not on any disk of this server"
        if source == from_disk.rstrip("/") or source == "":
            return f"{source} is a whole disk; move one folder, not everything"
        # Any database folder name anywhere in the path counts, e.g. ".../data/pg_wal".
        inside = source[len(from_disk.rstrip("/")):].lstrip("/")
        if touches_protected(server, source) or any(part in DATABASE_FOLDERS for part in inside.split("/")):
            return f"{source} holds the database's own data; moving it can destroy data"
        if action["to_mount"] not in server.disks:
            return f"there is no disk mounted at {action['to_mount']}"
        if action["to_mount"] == from_disk:
            return "the files would stay on the same disk"
        # The move must fit, or it fills the other disk and breaks whatever runs there.
        size = server.size_under(source)
        if size == 0:
            return f"nothing found at {source}"
        target = server.disks[action["to_mount"]]
        free = target.size_gb - target.used_gb
        if size > free:
            return f"{source} is {size:.1f} GB, but {action['to_mount']} has only {free:.1f} GB free"

    if kind == "disable_job":
        job = server.jobs.get(action["job"])
        if job is None:
            return f"there is no job called '{action['job']}' (jobs: {', '.join(server.jobs)})"
        if not job.enabled:
            return f"the job '{job.name}' is already turned off"

    return None


def check_steps(server: Server, title: str, steps) -> str | None:
    """Check every step of one option. Returns the reason the option is unsafe, or None if all steps are OK.

    One unsafe step blocks the whole option: half a fix can leave the server worse than before.
    """
    if not isinstance(steps, list) or not steps:
        return "no steps given"
    if len(steps) > MAX_STEPS:
        return f"{len(steps)} steps, but at most {MAX_STEPS} are allowed"

    pattern = find_forbidden(title)
    if pattern:
        return f"contains a forbidden command ({pattern})"
    for number, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return f"step {number} is not an action"
        # Forbidden words in any detail first, then the action itself.
        pattern = find_forbidden(" ".join(str(value) for value in step.values()))
        reason = f"contains a forbidden command ({pattern})" if pattern else check_action(server, step)
        if reason:
            return f"step {number}: {reason}"
    return None


def check_options(server: Server, options: list) -> tuple[list, list]:
    """Split Ultra's fix options into safe ones and blocked ones.

    Returns (safe, blocked). Each blocked entry is {"title": ..., "reason": ...}.
    Safe options get any missing display fields filled in, so the human always sees the same layout.
    """
    safe, blocked = [], []
    for option in options:
        title = str(option.get("title", "(no title)"))
        steps = option.get("steps")
        # A single "action" instead of "steps" is fine too: it's a fix with one step.
        if steps is None and isinstance(option.get("action"), dict):
            steps = [option.pop("action")]

        reason = check_steps(server, title, steps)
        if reason:
            blocked.append({"title": title, "reason": reason})
        else:
            safe.append({**DISPLAY_DEFAULTS, **option, "steps": steps})
    return safe, blocked

"""
validator.py
Checks a fault written by Ultra before it goes anywhere near the agent. Plain Python, no AI.

Prompt rules make mistakes rarer, never impossible (lesson 36): in our tests Ultra still rolled
back to a version nobody could see, or repeated an old change. Everything that can be checked
in code is checked here, in 3 groups:

1. Shape: every part is there, with the right type. Otherwise the code that loads it would crash.
2. Fits our server: only real containers, disks, URLs and fields, sensible numbers and times,
   no repeated old changes, no words that give the answer away, no damaged data or failing disks
   (nothing on the menu repairs those), log lines that don't contradict each other.
3. Fair and fixable: the fault really breaks something, every image the fix needs is visible
   on the board, and we find a fix ourselves (actions from the menu, checked by safety.py),
   run it on a test server and get the verdict FIXED. If our code can't win, the agent can't either.

validate_fault() returns the list of problems, in words Ultra can act on. An empty list = OK.
The fault maker sends the problems back to Ultra once; if they're still there, the fault is rejected.
Before that, clean_fault() fixes harmless mistakes in code (a copied old change, a timestamp
inside a log line), so a fault isn't rejected over something that loses nothing when fixed.
"""

import re

from app.faults import apply_fault
from app.fixes import apply_fix, holds, verify
from app.safety import MAX_STEPS, check_steps, touches_protected
from app.simulator import Server, minutes_ago

# Ultra is told to stay within the last 2 days.
MAX_MINUTES = 2 * 24 * 60

CATEGORIES = {"disk", "memory", "crash", "network", "config", "deploy", "other"}

# The keys apply_changes() in faults.py knows. Anything else would be silently ignored.
CHANGE_KEYS = {"containers", "disks", "files", "jobs", "recent_changes", "endpoints", "logs", "stop_normal_logs"}
CONTAINER_KEYS = {"image", "status", "exit_code", "restarts", "since_minutes_ago",
                  "cpu_percent", "memory_mb", "memory_limit_mb"}
STATUSES = {"running", "restarting", "exited"}

# The checks fixes.py knows, with the details each one needs.
CHECK_PARAMS = {
    "container_running": ["container"],
    "image": ["container", "is"],
    "memory_limit_mb": ["container", "at_least"],
    "disk_free_gb": ["mount", "at_least"],
    "job_enabled": ["job", "is"],
}

# Words that would tell the agent it's a game, or hand it the answer. "user-agent" is fine.
GIVEAWAY = re.compile(r"root cause|hidden|simulat|scenario|(?<![\w-])agent\b", re.IGNORECASE)

# Damaged data or a failing disk. No action on the menu can repair those, so the fault maker's rule 6
# forbids them, but a prompt rule wasn't enough: an evaluation fault (26 Oct) had "read-only filesystem or
# corrupted data" in its database logs, where a restart could only hope, and an agent that read those logs
# right was graded "partly".
UNFIXABLE = re.compile(r"corrupt|read-only file ?system|input/output error|\bI/O error|hardware|"
                       r"page verification failed|bad block", re.IGNORECASE)

# The tools add the time in front of every log line, so a line must not bring its own.
STARTS_WITH_DATE = re.compile(r"^\W*\d{4}-\d{2}-\d{2}")

# A request and its HTTP status, in our api style ("GET /api/products 200")
# and in nginx style ('"GET /products HTTP/1.1" 200').
REQUEST = re.compile(r'\b(GET|POST|PUT|PATCH|DELETE)\s+(/[^\s"]*)(?:\s+HTTP/[\d.]+")?\s+(\d{3})')

VERSION = re.compile(r"\d+\.\d+(?:\.\d+)?")


def is_number(value) -> bool:
    """True for 3 or 2.5, False for "3", True/False or None (in Python, True counts as a number)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_minutes(value) -> bool:
    """A time in "minutes ago" that fits the last 2 days."""
    return is_number(value) and 0 <= value <= MAX_MINUTES


def is_pair(item) -> bool:
    """A [minutes ago, text] pair, the format of log lines and recent changes."""
    return isinstance(item, list) and len(item) == 2 and isinstance(item[1], str) and item[1].strip() != ""


# --- 1. Shape ---------------------------------------------------------------------------------

def check_group(name: str, group, healthy: Server) -> list[str]:
    """Check one part of fixed_when: {"all": [...]} or {"any": [...]} of known checks with their details."""
    if not isinstance(group, dict) or len(group) != 1 or next(iter(group)) not in ("all", "any"):
        return [f'fixed_when.{name} must be {{"all": [...]}} or {{"any": [...]}}']
    rules = next(iter(group.values()))
    if not isinstance(rules, list) or not rules:
        return [f"fixed_when.{name} has no checks"]

    problems = []
    for rule in rules:
        kind = rule.get("check") if isinstance(rule, dict) else None
        if kind not in CHECK_PARAMS:
            problems.append(f"fixed_when.{name}: unknown check {kind!r} (allowed: {', '.join(CHECK_PARAMS)})")
            continue
        missing = [p for p in CHECK_PARAMS[kind] if p not in rule]
        if missing:
            problems.append(f"fixed_when.{name}: check '{kind}' is missing {', '.join(missing)}")
            continue
        if "container" in rule and rule["container"] not in healthy.containers:
            problems.append(f"fixed_when.{name}: there is no container '{rule['container']}'")
        if "mount" in rule and rule["mount"] not in healthy.disks:
            problems.append(f"fixed_when.{name}: there is no disk '{rule['mount']}'")
        if "at_least" in rule and not is_number(rule["at_least"]):
            problems.append(f"fixed_when.{name}: at_least must be a number")
        # The only action for jobs is disable_job, so "job is on" can never be reached by a fix.
        if kind == "job_enabled" and rule["is"] is not False:
            problems.append(f"fixed_when.{name}: job_enabled can only be checked for \"is\": false")
    return problems


def check_shape(fault, healthy: Server) -> list[str]:
    """Every part is there with the right type. If not, nothing else can be checked safely."""
    if not isinstance(fault, dict):
        return ["the fault must be a JSON object"]
    problems = [f"'{key}' must be a non-empty text" for key in ("title", "alert")
                if not isinstance(fault.get(key), str) or not fault[key].strip()]

    truth = fault.get("truth")
    if not isinstance(truth, dict):
        problems.append("'truth' must be an object with service, category, root_cause and fix")
    else:
        problems += [f"truth.{key} must be a non-empty text" for key in ("service", "category", "root_cause", "fix")
                     if not isinstance(truth.get(key), str) or not truth[key].strip()]
        if truth.get("service") not in healthy.containers:
            problems.append(f"truth.service must be one of {', '.join(healthy.containers)}")
        if truth.get("category") not in CATEGORIES:
            problems.append(f"truth.category must be one of {', '.join(sorted(CATEGORIES))}")

    for part in ("changes", "recovery"):
        value = fault.get(part, {})
        if not isinstance(value, dict):
            problems.append(f"'{part}' must be an object")
        elif set(value) - CHANGE_KEYS:
            problems.append(f"'{part}' has unknown keys {sorted(set(value) - CHANGE_KEYS)} "
                            f"(allowed: {', '.join(sorted(CHANGE_KEYS))})")

    fixed_when = fault.get("fixed_when")
    if not isinstance(fixed_when, dict) or not {"users_ok", "stays_fixed"} <= set(fixed_when):
        problems.append("'fixed_when' must have users_ok and stays_fixed")
    else:
        problems += check_group("users_ok", fixed_when["users_ok"], healthy)
        problems += check_group("stays_fixed", fixed_when["stays_fixed"], healthy)
    return problems


# --- 2. Fits our server -----------------------------------------------------------------------

def check_changes(part: str, changes: dict, healthy: Server) -> list[str]:
    """Check one 'changes' or 'recovery' block: real names, the right formats, sensible numbers and times."""
    problems = []

    for name, values in changes.get("containers", {}).items():
        if name not in healthy.containers:
            problems.append(f"{part}.containers: there is no container '{name}'")
            continue
        if not isinstance(values, dict):
            problems.append(f"{part}.containers.{name} must be an object")
            continue
        if set(values) - CONTAINER_KEYS:
            problems.append(f"{part}.containers.{name} has unknown keys {sorted(set(values) - CONTAINER_KEYS)}")
        if "status" in values and values["status"] not in STATUSES:
            problems.append(f"{part}.containers.{name}.status must be one of {', '.join(sorted(STATUSES))}")
        for key in ("exit_code", "restarts", "cpu_percent", "memory_mb", "memory_limit_mb"):
            if key in values and (not is_number(values[key]) or values[key] < 0):
                problems.append(f"{part}.containers.{name}.{key} must be a number of 0 or more")
        if "since_minutes_ago" in values and not is_number(values["since_minutes_ago"]):
            problems.append(f"{part}.containers.{name}.since_minutes_ago must be a number")

    for mount, values in changes.get("disks", {}).items():
        if mount not in healthy.disks:
            problems.append(f"{part}.disks: there is no disk '{mount}' (disks: {', '.join(healthy.disks)})")
        elif not isinstance(values, dict) or set(values) != {"used_gb"} or not is_number(values["used_gb"]):
            problems.append(f"{part}.disks.{mount} must be {{\"used_gb\": number}}")
        elif not 0 <= values["used_gb"] <= healthy.disks[mount].size_gb:
            problems.append(f"{part}.disks.{mount}.used_gb must be between 0 and the disk size "
                            f"({healthy.disks[mount].size_gb} GB)")

    for item in changes.get("files", []):
        if not (isinstance(item, list) and len(item) == 3 and isinstance(item[0], str)
                and is_number(item[1]) and item[1] > 0 and is_minutes(item[2])):
            problems.append(f"{part}.files: {item!r} must be [path, size_gb above 0, minutes ago]")
        elif healthy.disk_of(item[0]) is None:
            problems.append(f"{part}.files: {item[0]} is not on any disk")

    for job in changes.get("jobs", []):
        if not isinstance(job, dict) or not all(isinstance(job.get(k), str) and job[k] for k in
                                                ("name", "schedule", "command")):
            problems.append(f"{part}.jobs: {job!r} needs a name, schedule and command")

    old_changes = [text for _, text in healthy.changes]
    for item in changes.get("recent_changes", []):
        if not is_pair(item):
            problems.append(f"{part}.recent_changes: {item!r} must be [minutes ago (0-{MAX_MINUTES}), text]")
            continue
        if not is_minutes(item[0]):
            problems.append(f"{part}.recent_changes: '{item[1]}' is {item[0]} minutes ago; "
                            f"use 0-{MAX_MINUTES} (the last 2 days)")
        # Checked even if the time is wrong, so Ultra hears about both problems at once.
        for old in old_changes:
            # A copy, or a rewrite of an old change (same first word and the same version numbers).
            same_versions = VERSION.findall(item[1]) and set(VERSION.findall(item[1])) == set(VERSION.findall(old))
            if old in item[1] or (same_versions and item[1].split()[0] == old.split()[0]):
                problems.append(f"{part}.recent_changes: '{item[1]}' repeats the existing change '{old}'. "
                                "recent_changes are ADDED to the list; only add new events")
                break

    for url, result in changes.get("endpoints", {}).items():
        if url not in healthy.endpoints:
            problems.append(f"{part}.endpoints: unknown URL {url} (URLs: {', '.join(healthy.endpoints)})")
        elif not (isinstance(result, dict) and is_number(result.get("status")) and is_number(result.get("ms"))
                  and (result["status"] == 0 or 100 <= result["status"] <= 599) and result["ms"] >= 0):
            problems.append(f"{part}.endpoints.{url} must be {{\"status\": 0 or an HTTP code, \"ms\": number}}")

    for name, lines in changes.get("logs", {}).items():
        if name not in healthy.containers:
            problems.append(f"{part}.logs: there is no container '{name}'")
            continue
        for line in lines if isinstance(lines, list) else [lines]:
            if not is_pair(line) or not is_minutes(line[0]):
                problems.append(f"{part}.logs.{name}: {line!r} must be [minutes ago (0-{MAX_MINUTES}), text]")
            elif STARTS_WITH_DATE.match(line[1]):
                problems.append(f"{part}.logs.{name}: '{line[1][:60]}' starts with its own date; "
                                "the tools add the time, so leave it out")

    for name, minutes in changes.get("stop_normal_logs", {}).items():
        if name not in healthy.containers:
            problems.append(f"{part}.stop_normal_logs: there is no container '{name}'")
        elif not is_minutes(minutes):
            problems.append(f"{part}.stop_normal_logs.{name} must be minutes ago (0-{MAX_MINUTES})")
    return problems


def shown_texts(fault: dict) -> list[str]:
    """Every text from the fault that the agent can see: the alert, log lines, changes, jobs, file names."""
    changes = fault["changes"]
    texts = [fault["alert"]]
    texts += [line[1] for lines in changes.get("logs", {}).values() for line in lines]
    texts += [item[1] for item in changes.get("recent_changes", [])]
    texts += [job["command"] for job in changes.get("jobs", [])]
    texts += [item[0] for item in changes.get("files", [])]
    return texts


def check_giveaways(fault: dict) -> list[str]:
    """No visible text may hint that it's a game or name the answer outright."""
    return [f"'{text[:60]}' contains the word '{GIVEAWAY.search(text).group()}', which gives it away"
            for text in shown_texts(fault) if GIVEAWAY.search(text)]


def check_unfixable(fault: dict) -> list[str]:
    """No visible text may show damaged data or a failing disk: the actions on the menu can't repair those."""
    return [f"'{text[:60]}' shows damaged data or a failing disk ('{UNFIXABLE.search(text).group()}'); "
            "no action on the menu can repair that, so make a fault the actions can really fix"
            for text in shown_texts(fault) if UNFIXABLE.search(text)]


def check_times(fault: dict) -> list[str]:
    """The first error must not come before the change that started it."""
    changes = fault["changes"]
    change_times = [item[0] for item in changes.get("recent_changes", [])]
    log_times = [line[0] for lines in changes.get("logs", {}).values() for line in lines]
    if change_times and log_times and max(log_times) > max(change_times):
        return [f"the first new log line is {max(log_times)} minutes ago, but the oldest new change is only "
                f"{max(change_times)} minutes ago: the problem can't start before its cause"]
    return []


def check_server(server: Server) -> list[str]:
    """After loading: memory within the limits, and every disk's 'used' matches the files on it."""
    problems = []
    for c in server.containers.values():
        if c.memory_mb > c.memory_limit_mb:
            problems.append(f"{c.name} uses {c.memory_mb} MB but its limit is {c.memory_limit_mb} MB")
    limits = sum(c.memory_limit_mb for c in server.containers.values())
    if limits > server.memory_total_mb:
        problems.append(f"the memory limits add up to {limits} MB, more than the server's {server.memory_total_mb} MB")
    # du must explain df: if a disk is full, the files that fill it must be there to find.
    for mount, disk in server.disks.items():
        files = server.size_under(mount)
        if abs(files - disk.used_gb) > 0.5:
            problems.append(f"disk {mount} says {disk.used_gb} GB used, but its files add up to {files:.1f} GB; "
                            "add the files that fill it (and raise used_gb by the same amount)")
    return problems


def check_contradictions(fault: dict, server: Server) -> list[str]:
    """A request that fails in the new lines must not also succeed in the normal lines after the fault started.

    Allowed when something keeps restarting: then it really works now and then.
    """
    changes = fault["changes"]
    if any(values.get("status") in ("restarting", "exited") or values.get("restarts", 0) > 0
           for values in changes.get("containers", {}).values()):
        return []

    problems = []
    for name, lines in changes.get("logs", {}).items():
        new_texts = {line[1] for line in lines}
        failing = {(m.group(1), m.group(2)) for text in new_texts
                   for m in REQUEST.finditer(text) if int(m.group(3)) >= 500}
        if not failing:
            continue
        started = minutes_ago(max(line[0] for line in lines))
        for moment, text in server.containers[name].logs:
            match = REQUEST.search(text)
            if (moment >= started and text not in new_texts and match
                    and (match.group(1), match.group(2)) in failing and int(match.group(3)) < 400):
                problems.append(f"{name}: {match.group(1)} {match.group(2)} fails in the new log lines but the "
                                f"normal line '{text}' still shows it working; add \"stop_normal_logs\": "
                                f"{{\"{name}\": minutes}} so the normal lines stop when the problem starts")
                break
    return problems


# --- 3. Fair and fixable ----------------------------------------------------------------------

def visible_images(server: Server, healthy: Server) -> set[str]:
    """Images the agent can know about: what each container ran when healthy, what it runs now,
    and versions named in recent changes (like "Upgraded cache from redis 7.2 to 7.4")."""
    images = {c.image for c in healthy.containers.values()} | {c.image for c in server.containers.values()}
    for c in server.containers.values():
        program = c.image.split(":")[0]
        # Only changes that name this container or its program count: "redis 7.2" is a cache version, not an api one.
        names = re.compile(rf"\b({re.escape(c.name)}|{re.escape(program.split('/')[-1])})\b", re.IGNORECASE)
        for _, text in server.changes:
            if names.search(text):
                images |= {f"{program}:{version}" for version in VERSION.findall(text)}
    return images


def next_version(image: str) -> str:
    """The next patch version of an image, for examples: shopapp/api:2.3.1 -> shopapp/api:2.3.2."""
    program, _, tag = image.partition(":")
    parts = tag.split(".")
    if not parts[-1].isdigit():
        return f"{program}:{tag}-new"
    return f"{program}:{'.'.join(parts[:-1] + [str(int(parts[-1]) + 1)])}"


def step_for(rule: dict, server: Server) -> dict | None:
    """The menu action that makes one fixed_when check true, or None if there isn't one."""
    kind = rule["check"]
    if kind == "image":
        return {"type": "rollback", "container": rule["container"], "to_image": rule["is"]}
    if kind == "container_running":
        return {"type": "restart", "container": rule["container"]}
    if kind == "memory_limit_mb":
        return {"type": "set_memory_limit", "container": rule["container"], "memory_mb": int(rule["at_least"])}
    if kind == "job_enabled":
        return {"type": "disable_job", "job": rule["job"]}
    if kind == "disk_free_gb":
        # Move one file or folder that frees enough space and fits on another disk; the smallest
        # that does the job, like moving only the older backup.
        disk = server.disks[rule["mount"]]
        needed = rule["at_least"] - (disk.size_gb - disk.used_gb)
        for path, item in sorted(server.items_under(rule["mount"]).items(), key=lambda pair: pair[1].size_gb):
            if item.size_gb < needed or touches_protected(server, path):
                continue
            for mount, other in server.disks.items():
                if mount != rule["mount"] and item.size_gb <= other.size_gb - other.used_gb:
                    return {"type": "move_files", "from_path": path, "to_mount": mount}
    return None


def find_fix(fault: dict, server: Server) -> tuple[list[dict], list[str]]:
    """Build a fix from the menu that should make both users_ok and stays_fixed true.

    "all" needs a step for every check, "any" for one of them. Returns (steps, problems).
    """
    steps, problems = [], []
    for name in ("users_ok", "stays_fixed"):
        group = fault["fixed_when"][name]
        need_all = "all" in group
        rules = group["all" if need_all else "any"]
        found = [step for step in (step_for(rule, server) for rule in rules) if step]
        if need_all and len(found) < len(rules):
            problems.append(f"fixed_when.{name}: no action from the menu can make every check true")
        elif not found:
            problems.append(f"fixed_when.{name}: no action from the menu can make any check true")
        for step in found if need_all else found[:1]:
            if step not in steps:
                steps.append(step)
    return steps, problems


def check_fixable(fault: dict, server: Server, healthy: Server) -> list[str]:
    """It's broken now, the fix only needs visible things, and a safe fix from the menu gets FIXED."""
    rules = fault["fixed_when"]
    broken_now, _ = holds(server, rules["users_ok"])
    if broken_now:
        return ["fixed_when.users_ok is already true on the broken server, so there is nothing to fix"]

    problems = []
    visible = visible_images(server, healthy)
    for name in ("users_ok", "stays_fixed"):
        for rule in next(iter(rules[name].values())):
            if rule["check"] == "image" and rule["is"] not in visible:
                # A concrete way out works better than a rule (lesson from the guard): name the
                # images it can use and show the "new bad deploy" pattern with real names.
                container = rule["container"]
                now = healthy.containers[container].image
                options = sorted(i for i in visible if i.split(":")[0] == now.split(":")[0])
                problems.append(f"fixed_when.{name} needs {rule['is']}, but that version appears nowhere the "
                                f"tools can see. Visible {container} images: {', '.join(options)}. "
                                f"{now} has worked fine for days, so it is not the bad version. "
                                f"Make the bad version a NEW deploy during the fault (for example "
                                f"{next_version(now)}, deployed 20 minutes ago, in containers and recent_changes) "
                                f"and roll back to {now}")
    steps, fix_problems = find_fix(fault, server)
    problems += fix_problems
    if problems:
        return problems

    if len(steps) > MAX_STEPS:
        return [f"the fix needs {len(steps)} steps, but an option can have at most {MAX_STEPS}"]
    unsafe = check_steps(server, "validator's test fix", steps)
    if unsafe:
        return [f"the fix the checks ask for is blocked by the safety rules: {unsafe}"]
    apply_fix(server, {"approved": True, "option": {"steps": steps}, "by": "validator"})
    result = verify(server, fault)
    if result["verdict"] != "FIXED":
        return [f"after the fix {steps} the verdict is {result['verdict']}, not FIXED"]
    return []


# --- Cleaning: harmless mistakes are fixed, not rejected ----------------------------------------

# A full timestamp at the start of a log line, like "2026-10-22 18:35:13 " or "[2026-10-22T18:35:13.123Z] ".
LEADING_TIMESTAMP = re.compile(r"^\s*\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2})?(?:[.,]\d+)?"
                               r"(?:Z|[+-]\d{2}:?\d{2})?\]?\s*")


def clean_fault(fault) -> list[str]:
    """Fix mistakes that lose nothing when fixed, instead of failing the whole fault over them.

    Like the source link check: a typo is corrected, only real problems are reported.
    - an exact copy of a change the server already has is dropped (it's already on the list)
    - a timestamp at the start of a log line is cut off (the tools add the time themselves)
    Changes `fault` in place and returns one note per fix. Anything else is left to validate_fault().
    """
    notes = []
    if not isinstance(fault, dict):
        return notes
    old_changes = {text.strip().lower() for _, text in Server().changes}
    for part in ("changes", "recovery"):
        block = fault.get(part)
        if not isinstance(block, dict):
            continue

        items = block.get("recent_changes")
        if isinstance(items, list):
            kept = [item for item in items if not (is_pair(item) and item[1].strip().lower() in old_changes)]
            notes += [f"dropped '{item[1]}' from {part}.recent_changes: the server already lists it"
                      for item in items if item not in kept]
            block["recent_changes"] = kept

        logs = block.get("logs")
        if isinstance(logs, dict):
            for name, lines in logs.items():
                for line in lines if isinstance(lines, list) else []:
                    if is_pair(line) and LEADING_TIMESTAMP.match(line[1]):
                        line[1] = LEADING_TIMESTAMP.sub("", line[1])
                        notes.append(f"cut the timestamp off a {name} log line in {part}")
    return notes


# --- All together -----------------------------------------------------------------------------

def validate_fault(fault) -> list[str]:
    """Check a fault from Ultra. Returns the problems found, in words Ultra can act on; empty = OK."""
    healthy = Server()
    problems = check_shape(fault, healthy)
    if problems:
        return problems

    problems += check_changes("changes", fault["changes"], healthy)
    problems += check_changes("recovery", fault.get("recovery", {}), healthy)

    # The deeper checks run even if problems were found above, so Ultra hears about everything at once.
    try:
        problems += check_giveaways(fault) + check_unfixable(fault) + check_times(fault)
        server = Server()
        apply_fault(server, fault)
        problems += check_server(server) + check_contradictions(fault, server)
        problems += check_fixable(fault, server, healthy)
    except Exception as error:
        # A badly formed part (usually already listed above) can break the deeper checks.
        # That's one more problem to report, never a crash.
        problems.append(f"the fault could not be loaded and tested: {type(error).__name__}: {error}")
    return problems

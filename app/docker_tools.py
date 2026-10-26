"""
docker_tools.py
Real Docker mode: the same server tools as tools.py, but on real containers on this machine.

The agent can't tell the difference: every tool prints the same columns as the simulated one.
1. DockerServer is a Server (simulator.py) filled from real commands: docker inspect for the
   containers, df for the disks, real-docker/changes.log for the deploy history. So safety.py,
   the final call and the approval check the real server with exactly the same rules.
2. The read tools (READ_TOOLS) only run commands that read: docker inspect, docker logs,
   docker stats, df, du, crontab -l, and plain HTTP calls to the shop.
3. Fixes come from a smaller menu than in the simulator (ALLOWED_ACTIONS): restart, rollback and
   set_memory_limit. Moving files or turning off jobs would change the machine itself, so real
   mode doesn't do them. A fix still only runs after a human's approval (approval.py).
4. verify() checks what users see, for real: both shop URLs, and that no container keeps restarting.

Every command runs without a shell, as a list of words, so nothing the model writes can become
a second command. Run it with scripts/run_real.py on the server with the shop (real-docker/README.md).
The public demo never uses this file: it stays on the simulator.
"""

import functools
import http.client
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app import tools
from app.simulator import Container, Disk, Server

REAL_DOCKER = Path(__file__).resolve().parent.parent / "real-docker"
COMPOSE_FILE = REAL_DOCKER / "docker-compose.yml"
# The shop's deploy history. setup.sh and break.sh write to it, and so does every fix.
CHANGES_LOG = REAL_DOCKER / "changes.log"

# The same 4 containers as in the simulator, with the same names (docker-compose.yml sets them).
CONTAINERS = tools.CONTAINER_NAMES

# The variable in docker-compose.yml that picks each container's image, so a rollback can set it.
IMAGE_VARIABLES = {"web": "WEB_IMAGE", "api": "API_IMAGE", "db": "DB_IMAGE", "cache": "CACHE_IMAGE"}

# What a fix may do on a real server. move_files and disable_job stay simulator-only.
ALLOWED_ACTIONS = ["restart", "rollback", "set_memory_limit"]

# The shop's 2 health checks, the same URLs as in the simulator (the web container listens on port 80).
URLS = ["http://localhost/", "http://localhost/api/health"]

# Seconds before a command counts as hanging. Fixes get longer: starting a container takes time.
COMMAND_TIMEOUT = 30
FIX_TIMEOUT = 120
HTTP_TIMEOUT = 5

# File systems that aren't real disks; df leaves them out.
NOT_DISKS = ["tmpfs", "devtmpfs", "overlay", "squashfs", "efivarfs"]

# Only this many of the biggest entries are shown by check_folder_sizes, like the simulator's short lists.
MAX_FOLDER_LINES = 12

# While waiting for the shop to come back after a fix: seconds between checks.
CHECK_EVERY = 3

# A disk this full is a warning sign, the same line as in fixes.py.
NEARLY_FULL = 0.85


class DockerError(RuntimeError):
    """Docker didn't answer, or said no. The message says what happened, in Docker's own words."""


def run(command: list[str], timeout: float = COMMAND_TIMEOUT, env: dict | None = None) -> tuple[int, str, str]:
    """Run one command without a shell. Returns (exit code, output, error output).

    A missing program or a command that hangs comes back as an exit code and an error text too,
    so the agent reads what went wrong instead of the program crashing.
    Tests replace this function with one that returns saved docker output.
    """
    try:
        done = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, env={**os.environ, **(env or {})})
    except FileNotFoundError:
        return 127, "", f"{command[0]}: command not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"{' '.join(command)}: no answer after {timeout:.0f} seconds"
    return done.returncode, done.stdout, done.stderr


def docker_time(text: str) -> datetime | None:
    """Turn Docker's UTC time ('2026-10-26T12:00:00.123456789Z') into this machine's local time.

    Local time, like changes.log and the clock on the wall, so the agent can line up log lines
    and deploys. None for Docker's "never" (year 1, e.g. when a running container finished)
    and for anything that isn't a time.
    """
    if not text or text.startswith("0001-"):
        return None
    try:
        moment = datetime.strptime(text[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return moment.astimezone().replace(tzinfo=None)


def to_mb(size: str) -> float:
    """Docker's sizes like '35.2MiB', '1.5GiB' or '512KiB' in MiB. Anything unreadable counts as 0."""
    size = size.strip()
    # Longer units first: "MiB" also ends with "B".
    for unit, factor in (("KiB", 1 / 1024), ("MiB", 1), ("GiB", 1024), ("TiB", 1024 ** 2),
                         ("kB", 1000 / 1024 ** 2), ("MB", 1000 ** 2 / 1024 ** 2), ("GB", 1000 ** 3 / 1024 ** 2),
                         ("B", 1 / 1024 ** 2)):
        if size.endswith(unit):
            size, scale = size[:-len(unit)], factor
            break
    else:
        scale = 1 / 1024 ** 2
    try:
        return float(size) * scale
    except ValueError:
        return 0.0


def number(text: str) -> float:
    """A number from Docker's text like '0.15%', or 0 if Docker printed something else (like '--')."""
    try:
        return float(text.strip().rstrip("%"))
    except ValueError:
        return 0.0


def human_size(size: float) -> str:
    """A size in bytes the way 'du -h' writes it: 4.0K, 120M, 1.5G."""
    units = ["K", "M", "G", "T"]
    size, unit = size / 1024, 0
    while size >= 1024 and unit < len(units) - 1:
        size, unit = size / 1024, unit + 1
    return f"{size:.1f}{units[unit]}" if size < 10 else f"{size:.0f}{units[unit]}"


def read_memory_total() -> int:
    """The machine's memory in MB, from 'docker info': what all containers together can use."""
    code, out, err = run(["docker", "info", "--format", "{{.MemTotal}}"])
    if code != 0 or not out.strip().isdigit():
        raise DockerError(f"Docker is not reachable: {(err or out).strip()}")
    return int(out.strip()) // 1024 ** 2


def read_containers(memory_total_mb: int) -> dict[str, Container]:
    """The shop's containers from 'docker inspect', in the simulator's Container shape.

    A container that doesn't exist is left out, the same way 'docker ps -a' wouldn't list it.
    """
    code, out, err = run(["docker", "inspect", *CONTAINERS])
    # docker inspect prints what it found even when a name is missing (and exits with 1 then).
    try:
        found = json.loads(out or "[]")
    except ValueError:
        raise DockerError(f"docker inspect gave no readable answer: {(err or out).strip()[:200]}")
    if not found and code != 0 and "No such object" not in err:
        raise DockerError(err.strip() or "docker inspect failed")

    containers = {}
    for item in found:
        state = item["State"]
        status = state["Status"]
        # docker ps shows Up, Restarting or Exited; "created" and "dead" also mean "not running".
        if status not in ("running", "restarting"):
            status = "exited"
        # Up since the last start; Restarting and Exited since the last stop.
        since = docker_time(state["StartedAt"] if status == "running" else state["FinishedAt"])
        limit = item["HostConfig"].get("Memory") or 0
        name = item["Name"].lstrip("/")
        containers[name] = Container(
            name, item["Config"]["Image"], status=status, exit_code=state.get("ExitCode", 0),
            restarts=item.get("RestartCount", 0), since=since or datetime.now(),
            # No limit (0) means the container may use the whole machine.
            memory_limit_mb=max(1, round(limit / 1024 ** 2)) if limit else memory_total_mb)
    # The simulator's order, so the tables look the same in both modes.
    return {name: containers[name] for name in CONTAINERS if name in containers}


def read_disks() -> dict[str, Disk]:
    """The real disks from 'df', in the simulator's Disk shape (sizes in GB)."""
    command = ["df", "-B1", "--output=target,size,used,avail"]
    for kind in NOT_DISKS:
        command += ["-x", kind]
    code, out, err = run(command)
    disks = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) != 4 or not all(part.isdigit() for part in parts[1:]):
            continue
        mount, size, avail = parts[0], int(parts[1]), int(parts[3])
        # Boot partitions are tiny and never what fills up in a shop incident.
        if mount.startswith("/boot") or size == 0:
            continue
        # "Used" here is everything that isn't available, so Avail matches df -h exactly
        # (ext4 keeps about 5% for the system, which a program can't use either).
        disks[mount] = Disk(mount, size_gb=round(size / 1024 ** 3, 1), used_gb=round((size - avail) / 1024 ** 3, 1))
    if not disks:
        raise DockerError(f"df gave no disks: {(err or out).strip()[:200]}")
    return disks


def read_images() -> list[str]:
    """Every image on this machine as 'name:tag', so a rollback can only pick a version that is really here."""
    code, out, err = run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"])
    return sorted({line.strip() for line in out.splitlines() if line.strip() and "<none>" not in line})


def read_changes() -> list[tuple[datetime, str]]:
    """The deploy history from changes.log: one 'YYYY-MM-DD HH:MM:SS what changed' per line."""
    try:
        lines = CHANGES_LOG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    changes = []
    for line in lines:
        try:
            moment = datetime.strptime(line[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue  # a note without a time can't be lined up with the logs, so it's left out
        changes.append((moment, line[20:].strip()))
    return changes


def http_get(url: str, timeout: float = HTTP_TIMEOUT) -> tuple[int, int, str | None]:
    """Call a URL once, like curl. Returns (HTTP status, milliseconds, curl-style error or None).

    Status 0 means nothing answered at all, the same as in the simulator.
    Tests replace this function too.
    """
    # No proxy: the shop is on this machine.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    started = time.monotonic()
    try:
        with opener.open(url, timeout=timeout) as answer:
            status = answer.status
    except urllib.error.HTTPError as error:
        status = error.code  # 502, 503: the server answered, with an error
    except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
        reason = getattr(error, "reason", error)
        milliseconds = round((time.monotonic() - started) * 1000)
        # The same words curl uses, so the agent reads what an engineer would see.
        if isinstance(reason, TimeoutError):
            return 0, milliseconds, f"curl: (28) Operation timed out after {timeout * 1000:.0f} milliseconds"
        if isinstance(reason, ConnectionRefusedError):
            return 0, milliseconds, f"curl: (7) Failed to connect to {url}: Connection refused"
        return 0, milliseconds, f"curl: (56) Failure when receiving data from the peer ({reason})"
    return status, round((time.monotonic() - started) * 1000), None


class DockerServer(Server):
    """The real shop on this machine, in the same shape as the fake Server, so every rule works on both.

    It is a snapshot: refresh() reads the machine again, and each read tool refreshes what it shows.
    """

    # safety.py blocks every action that isn't in here.
    allowed_actions = ALLOWED_ACTIONS

    def __init__(self) -> None:
        """Read the whole machine once. Raises DockerError if Docker or the shop isn't there."""
        # Not Server.__init__(): that would build the fake shop. Everything comes from the real one.
        # tools.run_tool runs these real tools instead of the simulated ones.
        self.real_tools = READ_TOOLS
        self.files = {}  # du shows sizes live; nothing is kept, because files are never moved here
        self.protected_paths = []
        self.jobs = {}  # list_jobs shows the real crontab; jobs are never turned off here
        self.endpoints = {url: {"status": 0, "ms": 0} for url in URLS}
        self.refresh()
        if not self.containers:
            raise DockerError("none of the shop's containers (web, api, db, cache) exist on this machine. "
                              "Start the shop first: bash real-docker/setup.sh")

    def refresh(self) -> None:
        """Read everything again: memory, containers, disks, images and the deploy history."""
        self.memory_total_mb = read_memory_total()
        self.containers = read_containers(self.memory_total_mb)
        self.disks = read_disks()
        self.images = read_images()
        self.changes = read_changes()

    def add_change(self, text: str, minutes_back: float = 0) -> None:
        """Write a change to changes.log, the shop's real deploy history, and to this snapshot."""
        moment = datetime.now()
        with CHANGES_LOG.open("a", encoding="utf-8") as log:
            log.write(f"{moment:%Y-%m-%d %H:%M:%S} {text}\n")
        self.changes.append((moment, text))


def shows_errors(tool):
    """Wrap a real tool so a Docker or file problem comes back as text the agent can read, like in a terminal."""
    @functools.wraps(tool)
    def wrapper(*args, **kwargs):
        try:
            return tool(*args, **kwargs)
        except (DockerError, OSError) as error:
            return f"Error: {error}"
    return wrapper


@shows_errors
def list_containers(server: DockerServer) -> str:
    """Like 'docker ps -a', read live, in the simulator's columns."""
    server.containers = read_containers(server.memory_total_mb)
    return tools.list_containers(server)


@shows_errors
def get_logs(server: DockerServer, container: str, lines: int = 20) -> str:
    """Like 'docker logs --timestamps --tail N': the newest lines, in the simulator's format.

    Docker keeps a program's normal output and its error output apart (stdout and stderr);
    both are read and put back in time order, the way they were written.
    """
    if container not in CONTAINERS:
        return f"Error: No such container: {container}"
    code, out, err = run(["docker", "logs", "--timestamps", "--tail", str(lines), container])
    if code != 0:
        return f"Error: {(err or out).strip()}"
    entries = []
    for line in (out + "\n" + err).splitlines():
        stamp, _, text = line.rstrip("\r").partition(" ")
        moment = docker_time(stamp)
        if moment is not None:
            # The full stamp has nanoseconds, so sorting on it keeps lines in their exact order.
            entries.append((stamp, moment, text))
    entries.sort(key=lambda entry: entry[0])
    if not entries:
        return "(no log lines)"
    return "\n".join(f"{moment:%Y-%m-%d %H:%M:%S} {text}" for _, moment, text in entries[-lines:])


@shows_errors
def get_metrics(server: DockerServer) -> str:
    """Like 'docker stats --no-stream': live CPU and memory of every container, in the simulator's columns."""
    server.containers = read_containers(server.memory_total_mb)
    code, out, err = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *server.containers])
    for line in out.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        container = server.containers.get(row.get("Name"))
        if container is None:
            continue
        container.cpu_percent = number(row.get("CPUPerc", "0"))
        container.memory_mb = round(to_mb(row.get("MemUsage", "0B / 0B").split("/")[0]))
    return tools.get_metrics(server)


@shows_errors
def check_disk(server: DockerServer) -> str:
    """Like 'df -h', read live, in the simulator's columns."""
    server.disks = read_disks()
    return tools.check_disk(server)


def same_disk_entries(path: str) -> list[str]:
    """Everything directly inside a folder that is on the same disk as the folder (like du -x), sorted.

    Links are left out, and so is anything that vanishes or can't be read while we look.
    """
    disk = os.stat(path).st_dev
    found = []
    with os.scandir(path) as entries:
        for entry in entries:
            try:
                if not entry.is_symlink() and entry.stat(follow_symlinks=False).st_dev == disk:
                    found.append(entry.path)
            except OSError:
                continue
    return sorted(found)


def entries_to_measure(path: str) -> list[str] | str:
    """What du should measure for PATH: what's inside it on the same disk, or PATH itself if it's a file.

    Returns an error text (du's own words) if PATH doesn't exist or can't be read.
    """
    if not path.startswith("/") or not os.path.exists(path):
        return f"du: cannot access '{path}': No such file or directory"
    if not os.path.isdir(path):
        return [path]
    try:
        return same_disk_entries(path)
    except PermissionError:
        return f"du: cannot read directory '{path}': Permission denied"


@shows_errors
def check_folder_sizes(server: DockerServer, path: str) -> str:
    """Like 'du -xsh --time PATH/*' for real: what fills a folder, biggest first, only on the same disk."""
    entries = entries_to_measure(path.rstrip("/") or "/")
    if isinstance(entries, str):
        return entries
    if not entries:
        return f"{'0':<8}{'':<18}total"

    code, out, err = run(["du", "-x", "-s", "-B1", "--time", "--time-style=+%Y-%m-%d %H:%M", *entries], timeout=60)
    rows = []
    for line in out.splitlines():
        # du --time writes: size, tab, last changed, tab, path.
        size, _, rest = line.partition("\t")
        changed, _, item = rest.partition("\t")
        if size.isdigit() and item:
            rows.append((int(size), changed, item))
    rows.sort(reverse=True)
    lines = [f"{human_size(size):<8}{changed}  {item}" for size, changed, item in rows[:MAX_FOLDER_LINES]]
    if len(rows) > MAX_FOLDER_LINES:
        lines.append(f"(and {len(rows) - MAX_FOLDER_LINES} smaller ones)")
    lines.append(f"{human_size(sum(size for size, _, _ in rows)):<8}{'':<18}total")
    denied = err.count("Permission denied")
    if denied:
        lines.append(f"({denied} folder{'s' if denied > 1 else ''} could not be read: permission denied)")
    return "\n".join(lines)


@shows_errors
def list_jobs(server: DockerServer) -> str:
    """Like 'crontab -l': the scheduled jobs of the user Lifeguard runs as."""
    code, out, err = run(["crontab", "-l"])
    if code != 0:
        return (err or out).strip() or "no crontab"
    jobs = [line for line in out.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return "\n".join(jobs) if jobs else "no scheduled jobs (the crontab has only comments)"


@shows_errors
def recent_changes(server: DockerServer) -> str:
    """The deploy history from changes.log, in the simulator's format (newest first, with how long ago)."""
    server.changes = read_changes()
    return tools.recent_changes(server) or "No changes recorded in real-docker/changes.log."


@shows_errors
def check_endpoint(server: DockerServer, url: str) -> str:
    """Like 'curl': a real HTTP call to the shop, with the status code and the time it took."""
    if url not in URLS:
        return f"Error: unknown URL {url}. Known URLs: {', '.join(URLS)}"
    status, milliseconds, error = http_get(url)
    server.endpoints[url] = {"status": status, "ms": milliseconds}
    return error or f"HTTP {status} in {milliseconds} ms"


# Tool name -> its real version. search_past_incidents and search_web don't touch the server,
# so tools.py's own versions are used for them.
READ_TOOLS = {
    "list_containers": list_containers,
    "get_logs": get_logs,
    "get_metrics": get_metrics,
    "check_disk": check_disk,
    "check_folder_sizes": check_folder_sizes,
    "list_jobs": list_jobs,
    "recent_changes": recent_changes,
    "check_endpoint": check_endpoint,
}


def fix_command(step: dict) -> tuple[list[str], dict | None, str]:
    """The real command for one approved step: (command, extra environment, what it does in one line).

    Raises DockerError for anything real mode doesn't do. safety.py blocks those long before
    this point; this is a second lock on the same door.
    """
    kind, name = step.get("type"), step.get("container")
    if kind not in ALLOWED_ACTIONS:
        raise DockerError(f"'{kind}' is not allowed in real Docker mode (only {', '.join(ALLOWED_ACTIONS)})")
    if name not in CONTAINERS:
        raise DockerError(f"there is no container called '{name}'")
    if kind == "restart":
        return ["docker", "restart", name], None, f"restarted {name}"
    if kind == "rollback":
        # docker-compose.yml takes each image from a variable, so this starts only that one
        # container again (--no-deps leaves the others alone), from the older image.
        command = ["docker", "compose", "--file", str(COMPOSE_FILE), "up", "--detach", "--no-deps", name]
        return command, {IMAGE_VARIABLES[name]: step["to_image"]}, f"rolled back {name} to {step['to_image']}"
    memory = int(step["memory_mb"])
    # Swap limit = memory limit (no swap on top). Docker refuses a memory limit above an older
    # swap limit, so both are set together.
    command = ["docker", "update", "--memory", f"{memory}m", "--memory-swap", f"{memory}m", name]
    return command, None, f"set the memory limit of {name} to {memory} MB"


def apply_fix(server: DockerServer, decision: dict) -> list[str]:
    """Run every step of an approved option for real, in order, like fixes.apply_fix does on the fake server.

    Only an approved decision runs. If a step fails, the steps after it don't run: more changes on
    top of a failed step can leave the server worse. Each step that ran is written to changes.log.
    """
    if not decision.get("approved"):
        return []
    done = []
    for step in decision["option"]["steps"]:
        try:
            command, env, line = fix_command(step)
            code, out, err = run(command, timeout=FIX_TIMEOUT, env=env)
            if code != 0:
                raise DockerError(f"{' '.join(command)} failed: {(err or out).strip()}")
        except (DockerError, KeyError, ValueError) as error:  # KeyError/ValueError: a step with a missing or odd value
            done.append(f"FAILED: {error}")
            break
        server.add_change(f"Lifeguard-Agent fix: {line} (approved by {decision['by']})")
        done.append(line)
    return done


def check_now(server: DockerServer) -> dict[str, tuple[int, int, str | None]]:
    """Read the server again and call both shop URLs. Returns {url: (status, ms, error)}.

    If Docker doesn't answer this one time, the last reading is kept: the fix already ran, and the
    URLs still say what users see. Crashing here would hide the result of a real change.
    """
    try:
        server.refresh()
    except DockerError:
        pass
    return {url: http_get(url) for url in URLS}


def verify(server: DockerServer, wait_seconds: float = 75, steady_seconds: float = 10,
           pause=time.sleep) -> dict:
    """Check for real whether the shop works again, with the same result shape as fixes.verify.

    users_ok:    both shop URLs answer HTTP 200.
    stays_fixed: every container runs, and none restarted while we watched.
    A crash-looping container can look fine for a second, and Docker waits longer and longer
    between restarts (up to a minute), so this checks every few seconds for up to wait_seconds,
    then watches steady_seconds more before it trusts the result.
    """
    tries = max(1, int(wait_seconds // CHECK_EVERY))
    for number in range(tries):
        answers = check_now(server)
        working = (all(status == 200 for status, _, _ in answers.values())
                   and all(c.status == "running" for c in server.containers.values()))
        if working or number == tries - 1:
            break
        pause(CHECK_EVERY)

    restarts_before = {name: c.restarts for name, c in server.containers.items()}
    if working and steady_seconds:
        pause(steady_seconds)
        answers = check_now(server)

    users_ok = all(status == 200 for status, _, _ in answers.values())
    users_lines = [f"{'OK ' if status == 200 else 'NO '} {url} answers "
                   + (f"HTTP {status}" if status else (error or "nothing"))
                   for url, (status, _, error) in answers.items()]
    if len(users_lines) > 1:
        users_lines.insert(0, "(all of these are needed)")

    stays_lines, stays_fixed = [], True
    for name in CONTAINERS:
        container = server.containers.get(name)
        if container is None:
            stays_lines.append(f"NO  {name} doesn't exist")
            stays_fixed = False
            continue
        restarted = container.restarts - restarts_before.get(name, container.restarts)
        ok = container.status == "running" and restarted == 0
        stays_fixed = stays_fixed and ok
        note = f", restarted {restarted} time{'s' if restarted > 1 else ''} while we watched" if restarted else ""
        stays_lines.append(f"{'OK ' if ok else 'NO '} {name} is {container.status}{note}")
    stays_lines.insert(0, "(all of these are needed)")

    warnings = [f"{d.mount} is {d.used_gb / d.size_gb:.0%} full now ({d.size_gb - d.used_gb:.1f} GB free)"
                for d in server.disks.values() if d.size_gb and d.used_gb / d.size_gb >= NEARLY_FULL]
    if users_ok and stays_fixed:
        verdict = "FIXED"
    elif users_ok:
        verdict = "FIXED FOR NOW"
    else:
        verdict = "NOT FIXED"
    return {"verdict": verdict, "users_ok": users_lines, "stays_fixed": stays_lines, "warnings": warnings}

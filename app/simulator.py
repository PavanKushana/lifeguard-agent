"""
simulator.py
A pretend server running 4 Docker containers, so the agent has something safe to break and investigate.

Nothing here touches a real machine. The whole server is plain Python data:
containers (status, memory, restarts, logs), disks and the big files on them,
scheduled jobs, recent changes and health checks.
Faults (faults.py) change this data, and tools (tools.py) read it, the same way
real commands like "docker ps" or "df -h" read a real server.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

DAY = 24 * 60  # minutes in a day, to keep the numbers below readable


def minutes_ago(minutes: float) -> datetime:
    """Return the time a given number of minutes before now, for timestamps."""
    return datetime.now() - timedelta(minutes=minutes)


@dataclass
class Container:
    """One Docker container and everything the tools can see about it."""

    name: str
    image: str
    status: str = "running"  # "running", "restarting" or "exited", like in docker ps
    exit_code: int = 0  # 137 means the container was killed, often for using too much memory
    restarts: int = 0
    # When the current status began: "Up 12 days" or "Exited (137) 4 minutes ago" in docker ps.
    since: datetime = field(default_factory=lambda: minutes_ago(12 * DAY))
    cpu_percent: float = 1.0
    memory_mb: int = 100
    memory_limit_mb: int = 512
    # Each log line is (time, text), so lines added later by a fault still sort correctly.
    logs: list[tuple[datetime, str]] = field(default_factory=list)

    def add_log(self, text: str, minutes_back: float = 0) -> None:
        """Add one log line, as if it was written a given number of minutes ago."""
        self.logs.append((minutes_ago(minutes_back), text))


@dataclass
class Disk:
    """One mounted disk, like a line of 'df -h'."""

    mount: str
    size_gb: float
    used_gb: float


@dataclass
class StoredItem:
    """A big file or folder on a disk, like a line of 'du -sh': how big it is and when it last changed."""

    size_gb: float
    modified: datetime


@dataclass
class Job:
    """A scheduled job, like a line in the server's crontab."""

    name: str
    schedule: str  # e.g. "daily"
    command: str
    enabled: bool = True
    last_run: datetime | None = None


class Server:
    """The whole fake server. It starts healthy; faults break it, fixes repair it."""

    def __init__(self) -> None:
        """Build a healthy server: 4 containers, 2 disks with their files, 1 job, normal logs and a short change history."""
        # The machine's total memory. All container memory limits together must fit into it.
        self.memory_total_mb = 8192
        self.containers = {
            "web": Container("web", "nginx:1.27", cpu_percent=0.5,
                             memory_mb=25, memory_limit_mb=256),
            "api": Container("api", "shopapp/api:2.3.1", cpu_percent=4.0,
                             memory_mb=180, memory_limit_mb=512, since=minutes_ago(3 * DAY)),
            "db": Container("db", "postgres:16", cpu_percent=3.0,
                            memory_mb=320, memory_limit_mb=1024),
            "cache": Container("cache", "redis:7.4", cpu_percent=0.8,
                               memory_mb=60, memory_limit_mb=256, since=minutes_ago(9 * DAY)),
        }
        self.disks = {
            "/": Disk("/", size_gb=30, used_gb=11.2),
            "/var/lib/postgresql": Disk("/var/lib/postgresql", size_gb=50, used_gb=18.4),
        }
        # The big files and folders on the disks, so 'du' can show what fills a disk.
        # Their sizes add up to each disk's "used" above; a fault that adds files raises "used" too.
        self.files: dict[str, StoredItem] = {}
        self.add_file("/var/lib/docker", 8.9, 12 * DAY)
        self.add_file("/usr", 1.9, 30 * DAY)
        self.add_file("/var/log", 0.4, 10)
        self.add_file("/var/lib/postgresql/data", 18.4, 2)
        # Folders a fix must never move: the database's own data lives here.
        self.protected_paths = ["/var/lib/postgresql/data"]
        # Scheduled jobs, like the server's crontab.
        self.jobs: dict[str, Job] = {}
        self.add_job("rotate-logs", "daily", "logrotate /etc/logrotate.conf", last_run_minutes_ago=9 * 60)
        # Deploys and config changes. "What changed?" is the first question in any incident.
        self.changes = []
        self.add_change("Upgraded cache from redis 7.2 to 7.4", 9 * DAY)
        self.add_change("Deployed api 2.3.1 (fix checkout button)", 3 * DAY)
        # HTTP health checks: what curl would return for each URL.
        self.endpoints = {
            "http://localhost/": {"status": 200, "ms": 40},
            "http://localhost/api/health": {"status": 200, "ms": 65},
        }
        self._add_normal_logs()

    def add_change(self, text: str, minutes_back: float = 0) -> None:
        """Record a deploy or config change, as if it happened a given number of minutes ago."""
        self.changes.append((minutes_ago(minutes_back), text))

    def add_file(self, path: str, size_gb: float, minutes_back: float = 0) -> None:
        """Put a file or folder of a given size on the server, last changed a given number of minutes ago."""
        self.files[path] = StoredItem(size_gb, minutes_ago(minutes_back))

    def add_job(self, name: str, schedule: str, command: str,
                last_run_minutes_ago: float | None = None) -> None:
        """Add a scheduled job; last_run_minutes_ago is None if it never ran yet."""
        last_run = minutes_ago(last_run_minutes_ago) if last_run_minutes_ago is not None else None
        self.jobs[name] = Job(name, schedule, command, last_run=last_run)

    def disk_of(self, path: str) -> str | None:
        """Return the mount a path lives on (the longest matching mount), like 'df PATH' does."""
        matches = [m for m in self.disks if path == m or path.startswith(m.rstrip("/") + "/")]
        return max(matches, key=len) if matches else None

    def items_under(self, path: str) -> dict[str, StoredItem]:
        """Every file or folder at or inside `path` that is on the same disk as `path` (like 'du -x')."""
        path = path.rstrip("/") or "/"
        disk = self.disk_of(path)
        prefix = "/" if path == "/" else path + "/"
        return {p: item for p, item in self.files.items()
                if (p == path or p.startswith(prefix)) and self.disk_of(p) == disk}

    def size_under(self, path: str) -> float:
        """Total size in GB of everything at or inside `path` on its disk: what moving it would move."""
        return sum(item.size_gb for item in self.items_under(path).values())

    def _add_normal_logs(self) -> None:
        """Fill the containers with everyday log lines, so the agent has to find the signal in the noise."""
        web, api, db, cache = (self.containers[n] for n in ("web", "api", "db", "cache"))
        for m in (55, 41, 30, 18, 6, 2):
            web.add_log('172.18.0.1 - - "GET /products HTTP/1.1" 200 5123', m)
            api.add_log("INFO GET /api/products 200 in 42ms", m)
        api.add_log("INFO Connected to database db:5432", 3 * DAY)
        api.add_log("INFO Connected to cache cache:6379", 3 * DAY)
        db.add_log("LOG:  database system is ready to accept connections", 12 * DAY)
        db.add_log("LOG:  checkpoint starting: time", 35)
        db.add_log("LOG:  checkpoint complete: wrote 412 buffers (2.5%)", 34)
        db.add_log("LOG:  checkpoint starting: time", 5)
        db.add_log("LOG:  checkpoint complete: wrote 389 buffers (2.4%)", 4)
        cache.add_log("* Ready to accept connections tcp", 9 * DAY)
        cache.add_log("* 100 changes in 300 seconds. Saving...", 25)
        cache.add_log("* Background saving terminated with success", 25)

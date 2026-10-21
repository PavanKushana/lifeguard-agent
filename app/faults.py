"""
faults.py
Ways to break the fake server, written as data instead of code.

Every fault has three parts:
- alert:   what a user or monitor reports. This is the ONLY part the agent sees.
- truth:   the real cause and fix. Hidden from the agent; the grader uses it later.
- changes: what to change in the Server (disks, files, jobs, containers, logs, health checks, deploys).

Because a fault is plain data, the presets below and the custom faults that
Ultra writes from a user's text (Day 6) use exactly the same format.
"""

from app.simulator import DAY, Server, minutes_ago

# The backup job writes to the same disk as the database, until the disk is full.
# Reads still work, only writes fail, so the health check stays green.
DISK_FULL = {
    "id": "disk_full",
    "title": "Database disk full",
    "alert": "Customers can browse the shop, but placing an order fails with 'Something went wrong'.",
    "truth": {
        "service": "db",
        "category": "disk",
        "root_cause": "The disk /var/lib/postgresql is 100% full because the new nightly backup "
                      "job saves its dumps on the same disk, so PostgreSQL cannot write new data.",
        "fix": "Move the older backup file from /var/lib/postgresql/backups to another disk to free space "
               "(both dumps together don't fit on /), stop the backup job from filling the disk again, "
               "then check that orders work again. Long term: store backups on another disk.",
    },
    "changes": {
        # 18.4 GB of data + 2 dumps of 15.8 GB = 50.0 GB, the whole disk.
        "disks": {"/var/lib/postgresql": {"used_gb": 50.0}},
        # [path, size in GB, minutes ago]. "{date}" becomes the file's date, like real backup names.
        # The job was added 2 days ago and runs daily, so it has made 2 dumps so far.
        "files": [
            ["/var/lib/postgresql/backups/shop_{date}.dump", 15.8, DAY + 13 * 60],
            ["/var/lib/postgresql/backups/shop_{date}.dump", 15.8, 13 * 60],
        ],
        "jobs": [
            {"name": "db-backup", "schedule": "daily", "last_run_minutes_ago": 13 * 60,
             "command": "pg_dump -U shop shop > /var/lib/postgresql/backups/shop_$(date +%F).dump"},
        ],
        "recent_changes": [
            [2 * DAY, "Added nightly database backup job (pg_dump to /var/lib/postgresql/backups)"],
        ],
        "logs": {
            "db": [
                [14, 'ERROR:  could not extend file "base/16384/24576": No space left on device'],
                [14, "HINT:  Check free disk space."],
                [6, 'ERROR:  could not write to file "pg_wal/xlogtemp.123": No space left on device'],
                [2, 'ERROR:  could not extend file "base/16384/24576": No space left on device'],
            ],
            "api": [
                [14, "ERROR POST /api/orders 500: database error, see database logs"],
                [6, "ERROR POST /api/orders 500: database error, see database logs"],
                [2, "ERROR POST /api/orders 500: database error, see database logs"],
            ],
        },
    },
}

# A new report page runs a huge query; PostgreSQL goes over its 1 GB memory limit,
# gets killed (signal 9) and restarts again and again.
DB_OUT_OF_MEMORY = {
    "id": "db_out_of_memory",
    "title": "Database out of memory",
    "alert": "The new sales report page shows an error, and the whole shop keeps going down for a minute.",
    "truth": {
        "service": "db",
        "category": "memory",
        "root_cause": "api 2.4.0 added a sales report query that makes PostgreSQL use more than its "
                      "1 GB memory limit, so it is killed (out of memory, signal 9) and keeps restarting.",
        "fix": "Roll back api to 2.3.1 (or turn off the report page), then check the database stays up. "
               "Long term: fix the report query or give the database more memory.",
    },
    "changes": {
        "containers": {
            "db": {"restarts": 3, "since_minutes_ago": 2, "memory_mb": 1003, "cpu_percent": 85.0},
            "api": {"image": "shopapp/api:2.4.0", "since_minutes_ago": 40},
        },
        "recent_changes": [[40, "Deployed api 2.4.0 (new sales report page)"]],
        "endpoints": {"http://localhost/api/health": {"status": 503, "ms": 12}},
        "logs": {
            "api": [
                [40, "INFO Starting shopapp api 2.4.0"],
                [37, "ERROR GET /api/reports/sales 500: server closed the connection unexpectedly"],
                [19, "ERROR GET /api/reports/sales 500: server closed the connection unexpectedly"],
                [18, "ERROR GET /api/products 503: connection to db:5432 refused"],
                [3, "ERROR GET /api/reports/sales 500: server closed the connection unexpectedly"],
                [2, "ERROR GET /api/products 503: connection to db:5432 refused"],
            ],
            "db": [
                [37, "LOG:  server process (PID 2211) was terminated by signal 9: Killed"],
                [37, "DETAIL:  Failed process was running: SELECT o.*, c.*, p.* FROM orders o "
                     "JOIN customers c ON c.id = o.customer_id JOIN products p ON p.id = o.product_id "
                     "ORDER BY o.created_at"],
                [36, "LOG:  database system was not properly shut down; automatic recovery in progress"],
                [19, "LOG:  server process (PID 2547) was terminated by signal 9: Killed"],
                [18, "LOG:  database system was not properly shut down; automatic recovery in progress"],
                [3, "LOG:  server process (PID 2903) was terminated by signal 9: Killed"],
                [2, "LOG:  database system was not properly shut down; automatic recovery in progress"],
                [2, "LOG:  database system is ready to accept connections"],
            ],
        },
    },
}

PRESETS = {fault["id"]: fault for fault in (DISK_FULL, DB_OUT_OF_MEMORY)}


def apply_fault(server: Server, fault: dict) -> None:
    """Break the server the way the fault describes. Only 'changes' is used; the truth stays hidden."""
    changes = fault["changes"]

    for mount, values in changes.get("disks", {}).items():
        for key, value in values.items():
            setattr(server.disks[mount], key, value)

    for path, size_gb, minutes_back in changes.get("files", []):
        date = f"{minutes_ago(minutes_back):%Y-%m-%d}"
        server.add_file(path.replace("{date}", date), size_gb, minutes_back)

    for job in changes.get("jobs", []):
        server.add_job(job["name"], job["schedule"], job["command"], job.get("last_run_minutes_ago"))

    for name, values in changes.get("containers", {}).items():
        container = server.containers[name]
        for key, value in values.items():
            # Faults are plain data (no Python dates), so times are given as "minutes ago".
            if key == "since_minutes_ago":
                container.since = minutes_ago(value)
            else:
                setattr(container, key, value)

    for minutes_back, text in changes.get("recent_changes", []):
        server.add_change(text, minutes_back)

    for url, result in changes.get("endpoints", {}).items():
        server.endpoints[url] = result

    for name, lines in changes.get("logs", {}).items():
        for minutes_back, text in lines:
            server.containers[name].add_log(text, minutes_back)

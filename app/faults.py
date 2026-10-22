"""
faults.py
Ways to break the fake server, written as data instead of code.

Every fault has these parts:
- alert:      what a user or monitor reports. This is the ONLY part the agent sees.
- truth:      the real cause and fix. Hidden from the agent; the grader uses it later.
- changes:    what to change in the Server (disks, files, jobs, containers, logs, health checks, deploys).
              "stop_normal_logs" removes a container's normal log lines from a given time on.
- fixed_when: after a fix, how to tell if it worked (checked by fixes.py, also hidden):
              "users_ok"    = what users see is fine again,
              "stays_fixed" = the cause is gone, so it won't come back.
              Each is {"all": [...]} or {"any": [...]} of checks like {"check": "disk_free_gb", ...}.
- recovery:   how the server looks once users are OK again (same format as changes).

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
    "fixed_when": {
        # PostgreSQL needs some room to write new orders; 1 GB is plenty for a day.
        "users_ok": {"all": [{"check": "disk_free_gb", "mount": "/var/lib/postgresql", "at_least": 1.0}]},
        # Each night the job writes another 15.8 GB onto the same disk, so it must be off.
        "stays_fixed": {"all": [{"check": "job_enabled", "job": "db-backup", "is": False}]},
    },
    "recovery": {
        "logs": {
            "api": [[0, "INFO POST /api/orders 201 in 58ms"]],
            "db": [[0, "LOG:  checkpoint complete: wrote 97 buffers (0.6%)"]],
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
    "fixed_when": {
        # The report query needs about 1.4 GB. Without it (2.3.1) the db is fine with 1 GB,
        # and with it the db only survives if its limit is raised to at least that.
        "users_ok": {"any": [
            {"check": "image", "container": "api", "is": "shopapp/api:2.3.1"},
            {"check": "memory_limit_mb", "container": "db", "at_least": 1400},
        ]},
        # More memory only buys time: as the orders table grows, the query needs more.
        "stays_fixed": {"all": [{"check": "image", "container": "api", "is": "shopapp/api:2.3.1"}]},
    },
    "recovery": {
        "containers": {"db": {"status": "running", "since_minutes_ago": 0, "memory_mb": 340, "cpu_percent": 3.0}},
        "endpoints": {"http://localhost/api/health": {"status": 200, "ms": 65}},
        "logs": {
            "api": [[0, "INFO GET /api/products 200 in 42ms"]],
            "db": [[0, "LOG:  checkpoint complete: wrote 210 buffers (1.3%)"]],
        },
    },
}

# A new api version imports a library its image doesn't have, so it crashes on start, and Docker
# keeps restarting it. Without the api, nginx can't reach anything and every page is a 502.
API_CRASH_LOOP = [
    "INFO Starting shopapp api 2.3.2",
    "Traceback (most recent call last):",
    '  File "/app/shopapp/config.py", line 3, in <module>',
    "    from pydantic_settings import BaseSettings",
    "ModuleNotFoundError: No module named 'pydantic_settings'",
]
WEB_NO_UPSTREAM = ('[error] 29#29: *1841 connect() failed (111: Connection refused) while connecting to '
                   'upstream, client: 172.18.0.1, server: _, request: "GET /products HTTP/1.1", '
                   'upstream: "http://172.18.0.3:8000/api/products"')

BAD_DEPLOY = {
    "id": "bad_deploy",
    "title": "API crashes after a deploy",
    "alert": "Every page of the shop shows '502 Bad Gateway', for about 20 minutes now.",
    "truth": {
        "service": "api",
        "category": "deploy",
        "root_cause": "api 2.3.2 imports the pydantic_settings library, but it isn't installed in the image, "
                      "so the app crashes on start (exit code 1) and Docker keeps restarting it.",
        "fix": "Roll back api to 2.3.1, then check the pages load. Long term: start every new image once "
               "in the build pipeline before it's deployed.",
    },
    "changes": {
        "containers": {"api": {"image": "shopapp/api:2.3.2", "status": "restarting", "exit_code": 1,
                               "restarts": 14, "since_minutes_ago": 1}},
        "recent_changes": [[21, "Deployed api 2.3.2 (settings moved to pydantic-settings)"]],
        "endpoints": {"http://localhost/": {"status": 502, "ms": 3},
                      "http://localhost/api/health": {"status": 502, "ms": 2}},
        "stop_normal_logs": {"api": 21, "web": 21},
        "logs": {
            # The same crash on every restart; three of them are enough to see the pattern.
            "api": [[minute, line] for minute in (21, 11, 1) for line in API_CRASH_LOOP],
            "web": [line for minute in (20, 12, 2) for line in
                    ([minute, WEB_NO_UPSTREAM], [minute, '172.18.0.1 - - "GET /products HTTP/1.1" 502 157'])],
        },
    },
    "fixed_when": {
        "users_ok": {"all": [{"check": "image", "container": "api", "is": "shopapp/api:2.3.1"}]},
        "stays_fixed": {"all": [{"check": "image", "container": "api", "is": "shopapp/api:2.3.1"}]},
    },
    "recovery": {
        "containers": {"api": {"status": "running", "exit_code": 0, "restarts": 0, "since_minutes_ago": 0}},
        "endpoints": {"http://localhost/": {"status": 200, "ms": 40},
                      "http://localhost/api/health": {"status": 200, "ms": 65}},
        "logs": {
            "api": [[0, "INFO Starting shopapp api 2.3.1"], [0, "INFO GET /api/products 200 in 42ms"]],
            "web": [[0, '172.18.0.1 - - "GET /products HTTP/1.1" 200 5123']],
        },
    },
}

# A new api version reads the database password from a different setting, gets it wrong,
# and PostgreSQL refuses every login. The api still runs, so docker ps looks fine.
WRONG_DB_PASSWORD = {
    "id": "wrong_db_password",
    "title": "Wrong database password after a deploy",
    "alert": "The home page loads, but every product page says 'Service unavailable'.",
    "truth": {
        "service": "api",
        "category": "config",
        "root_cause": "api 2.3.2 reads the database password from a new setting that was never filled in, "
                      "so PostgreSQL rejects every login from the api.",
        "fix": "Roll back api to 2.3.1, then check the product pages. Long term: check all required "
               "settings when the app starts, so a missing one stops the deploy instead of the shop.",
    },
    "changes": {
        "containers": {"api": {"image": "shopapp/api:2.3.2", "since_minutes_ago": 35}},
        "recent_changes": [[35, "Deployed api 2.3.2 (database settings read from environment)"]],
        "endpoints": {"http://localhost/api/health": {"status": 503, "ms": 15}},
        "stop_normal_logs": {"api": 35, "web": 35},
        "logs": {
            "api": [[35, "INFO Starting shopapp api 2.3.2"]] + [
                [minute, 'ERROR GET /api/products 503: database connection failed: FATAL:  '
                         'password authentication failed for user "shop"'] for minute in (34, 22, 9, 1)],
            "db": [line for minute in (34, 22, 9, 1) for line in (
                [minute, 'FATAL:  password authentication failed for user "shop"'],
                [minute, 'DETAIL:  Connection matched file "/var/lib/postgresql/data/pg_hba.conf" '
                         'line 100: "host all all all scram-sha-256"'])],
            "web": [[minute, '172.18.0.1 - - "GET /products HTTP/1.1" 503 197'] for minute in (34, 22, 9, 1)],
        },
    },
    "fixed_when": {
        "users_ok": {"all": [{"check": "image", "container": "api", "is": "shopapp/api:2.3.1"}]},
        "stays_fixed": {"all": [{"check": "image", "container": "api", "is": "shopapp/api:2.3.1"}]},
    },
    "recovery": {
        "endpoints": {"http://localhost/api/health": {"status": 200, "ms": 65}},
        "logs": {
            "api": [[0, "INFO Starting shopapp api 2.3.1"], [0, "INFO Connected to database db:5432"],
                    [0, "INFO GET /api/products 200 in 42ms"]],
            "web": [[0, '172.18.0.1 - - "GET /products HTTP/1.1" 200 5123']],
        },
    },
}

# Redis hits a rare bug and crashes once (signal 11). The container has no restart policy,
# so it stays down: products still load from the database, but login and cart need the cache.
CACHE_CRASHED = {
    "id": "cache_crashed",
    "title": "Cache crashed and stayed down",
    "alert": "Customers can see products, but they can't log in or add anything to the cart.",
    "truth": {
        "service": "cache",
        "category": "crash",
        "root_cause": "Redis crashed once on a rare bug (signal 11, segmentation fault). The container has "
                      "no restart policy, so it stayed down, and login and cart, which keep sessions in "
                      "Redis, fail.",
        "fix": "Restart the cache, then check login and cart. Long term: give the containers a restart "
               "policy (restart: unless-stopped) and watch for a Redis update that fixes the bug.",
    },
    "changes": {
        # Exit code 139 = 128 + 11: killed by signal 11, a crash, not out of memory (that would be 137).
        "containers": {"cache": {"status": "exited", "exit_code": 139, "since_minutes_ago": 25}},
        "endpoints": {"http://localhost/api/health": {"status": 503, "ms": 8}},
        "logs": {
            "cache": [
                [25, "=== REDIS BUG REPORT START: Cut & paste starting from here ==="],
                [25, "# Redis 7.4.0 crashed by signal: 11, si_code: 1"],
                [25, "# Accessing address: 0x7f3a2c000010"],
                [25, "# Crashed running the instruction at: 0x55d1c2a3f1b4"],
                [25, "=== REDIS BUG REPORT END. Make sure to include from START to END. ==="],
            ],
            "api": [
                [24, "ERROR POST /api/login 500: Error 111 connecting to cache:6379. Connection refused."],
                [17, "ERROR POST /api/cart 500: Error 111 connecting to cache:6379. Connection refused."],
                [10, "ERROR GET /api/health 503: Error 111 connecting to cache:6379. Connection refused."],
                [3, "ERROR POST /api/cart 500: Error 111 connecting to cache:6379. Connection refused."],
            ],
        },
    },
    "fixed_when": {
        # A one-off crash: once Redis runs again, nothing is left that would crash it right away.
        "users_ok": {"all": [{"check": "container_running", "container": "cache"}]},
        "stays_fixed": {"all": [{"check": "container_running", "container": "cache"}]},
    },
    "recovery": {
        "containers": {"cache": {"status": "running", "exit_code": 0, "since_minutes_ago": 0}},
        "endpoints": {"http://localhost/api/health": {"status": 200, "ms": 65}},
        "logs": {
            "cache": [[0, "* Ready to accept connections tcp"]],
            "api": [[0, "INFO POST /api/cart 200 in 35ms"]],
        },
    },
}

# The chain fault: the first red service is NOT the cause.
# A new export job was meant to run daily, but runs every 5 minutes. Its huge query keeps the
# database busy, so the api's queries time out, its workers get killed, and nginx answers 502.
# What users see is web (502), the errors are in api, the cause is a job hitting db.
SALES_QUERY = ("SELECT o.id, o.created_at, c.email, SUM(i.qty * i.price) FROM orders o "
               "JOIN customers c ON c.id = o.customer_id JOIN order_items i ON i.order_id = o.id "
               "GROUP BY o.id, c.email ORDER BY o.created_at")

SLOW_DB_CHAIN = {
    "id": "slow_db_chain",
    "title": "Export job slows the database (chain)",
    "alert": "The shop is very slow and many pages end in '502 Bad Gateway'. It started less than an hour ago.",
    "truth": {
        "service": "db",
        "category": "config",
        "root_cause": "The new sales-export job was meant to run daily but runs every 5 minutes. Its huge "
                      "export query keeps PostgreSQL busy, so the api's queries time out, its workers are "
                      "killed, and nginx answers 502. web and api only show the effect.",
        "fix": "Turn off the sales-export job, then check the pages are fast again. Long term: run it "
               "daily at night, against a read-only copy of the database.",
    },
    "changes": {
        # psql's \copy runs on the server as "COPY (...) TO STDOUT", which is what the db log shows.
        "jobs": [{"name": "sales-export", "schedule": "every 5 minutes", "last_run_minutes_ago": 1,
                  "command": f'psql -U shop -c "\\copy ({SALES_QUERY}) TO /srv/exports/sales.csv CSV HEADER"'}],
        "recent_changes": [[48, "Added sales-export job for the accounting team (daily sales CSV)"]],
        "containers": {"db": {"cpu_percent": 97.0, "memory_mb": 880}, "api": {"cpu_percent": 1.5}},
        "endpoints": {"http://localhost/": {"status": 502, "ms": 30004},
                      "http://localhost/api/health": {"status": 200, "ms": 9200}},
        "stop_normal_logs": {"api": 46, "web": 46},
        "logs": {
            # Every run takes about 4.5 minutes, so the next one starts right after the last one ends.
            "db": [[minute, f"LOG:  duration: 268113.402 ms  statement: COPY ({SALES_QUERY}) TO STDOUT CSV HEADER"]
                   for minute in (41, 36, 31, 26, 21, 16, 11, 6, 1)] + [
                   line for minute in (39, 24, 8) for line in (
                       [minute, "ERROR:  canceling statement due to statement timeout"],
                       [minute, "STATEMENT:  SELECT id, name, price FROM products WHERE category_id = $1"])],
            # A killed worker is replaced by a new one, so each timeout has its own process id
            # (and nginx its own connection number), like on a real server.
            "api": [line for minute, pid in ((39, 41), (24, 57), (8, 73)) for line in (
                        [minute, "ERROR GET /api/products 500: canceling statement due to statement timeout"],
                        [minute, f"CRITICAL WORKER TIMEOUT (pid:{pid})"])] + [
                    [minute, "INFO GET /api/products 200 in 8412ms"] for minute in (33, 15, 2)],
            "web": [line for minute, conn in ((39, 2210), (24, 2387), (8, 2561)) for line in (
                        [minute, f'[error] 29#29: *{conn} upstream prematurely closed connection while reading '
                                 'response header from upstream, client: 172.18.0.1, server: _, request: '
                                 '"GET /products HTTP/1.1", upstream: "http://172.18.0.3:8000/api/products"'],
                        [minute, '172.18.0.1 - - "GET /products HTTP/1.1" 502 157'])],
        },
    },
    "fixed_when": {
        # Once the job is off, the last export finishes and the database is fast again.
        "users_ok": {"all": [{"check": "job_enabled", "job": "sales-export", "is": False}]},
        "stays_fixed": {"all": [{"check": "job_enabled", "job": "sales-export", "is": False}]},
    },
    "recovery": {
        "containers": {"db": {"cpu_percent": 3.0, "memory_mb": 340}, "api": {"cpu_percent": 4.0}},
        "endpoints": {"http://localhost/": {"status": 200, "ms": 40},
                      "http://localhost/api/health": {"status": 200, "ms": 65}},
        "logs": {
            "api": [[0, "INFO GET /api/products 200 in 42ms"]],
            "web": [[0, '172.18.0.1 - - "GET /products HTTP/1.1" 200 5123']],
        },
    },
}

PRESETS = {fault["id"]: fault for fault in (DISK_FULL, DB_OUT_OF_MEMORY, BAD_DEPLOY, WRONG_DB_PASSWORD,
                                            CACHE_CRASHED, SLOW_DB_CHAIN)}


def apply_fault(server: Server, fault: dict) -> None:
    """Break the server the way the fault describes. Only 'changes' is used; the truth stays hidden."""
    apply_changes(server, fault["changes"])


def apply_changes(server: Server, changes: dict) -> None:
    """Change the server as described: used to break it (a fault's changes) and to heal it (its recovery)."""
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

    # A broken service stops writing its normal lines. Without this, "GET /api/products 200" would keep
    # showing up between the errors and the service would look half-working. Runs before the fault's
    # own lines are added, so only the lines already there (the normal ones) are removed.
    for name, minutes_back in changes.get("stop_normal_logs", {}).items():
        cutoff = minutes_ago(minutes_back)
        container = server.containers[name]
        container.logs = [(moment, text) for moment, text in container.logs if moment < cutoff]

    for name, lines in changes.get("logs", {}).items():
        for minutes_back, text in lines:
            server.containers[name].add_log(text, minutes_back)

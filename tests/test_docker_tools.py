"""
test_docker_tools.py
Checks real Docker mode (docker_tools.py) without Docker: a fake machine answers every command
with saved output, the way docker, df, du and crontab really print it, and records what ran.

What's checked: the real tools print the same columns as the simulated ones, the safety check
blocks what real mode doesn't do, an approved fix runs the right docker command (and nothing
runs without approval), and the live check says FIXED only when the shop really works.
Run all tests with:  python -m pytest
"""

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app import docker_tools, tools
from app.agent import inventory
from app.docker_tools import DockerError, DockerServer
from app.safety import check_options
from app.simulator import Server
from scripts.run_real import make_alert

NOW = datetime.now(timezone.utc)

# What df, docker images, docker stats and du print on a small server (the sizes are in bytes).
DF = """Mounted on          1B-blocks         Used        Avail
/                 31580405760  12884901888  17179869184
/boot/efi           109422592      6291456    103131136
"""
IMAGES = "lifeguard/real-api:2.3.2\nlifeguard/real-api:2.3.1\nnginx:1.27-alpine\npostgres:16-alpine\n" \
         "redis:7.4-alpine\n<none>:<none>\n"
STATS = "\n".join(json.dumps(row) for row in [
    {"Name": "web", "CPUPerc": "0.02%", "MemUsage": "3.1MiB / 256MiB"},
    {"Name": "api", "CPUPerc": "0.00%", "MemUsage": "0B / 0B"},
    {"Name": "db", "CPUPerc": "1.50%", "MemUsage": "45.5MiB / 512MiB"},
    {"Name": "cache", "CPUPerc": "--", "MemUsage": "2.2MiB / 256MiB"},
])
DU = ("12884901888\t2026-10-26 11:00\t/var/lib/docker\n"
      "1073741824\t2026-10-26 12:00\t/var/log\n"
      "4096\t2026-01-01 10:00\t/var/tmp\n")


def stamp(minutes_ago: float) -> str:
    """A time the way Docker writes it: UTC, with 9 digits after the second."""
    moment = NOW - timedelta(minutes=minutes_ago)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{moment.microsecond:06d}000Z"


def container(name: str, image: str, status: str = "running", exit_code: int = 0, restarts: int = 0,
              memory_mb: int = 512) -> dict:
    """One container as 'docker inspect' describes it (only the fields Lifeguard reads)."""
    return {"Name": f"/{name}", "Config": {"Image": image}, "RestartCount": restarts,
            # Started 3 days and 1 hour ago: exactly 3 days would already read "2 days" a moment later.
            "State": {"Status": status, "ExitCode": exit_code, "StartedAt": stamp(3 * 24 * 60 + 60),
                      "FinishedAt": "0001-01-01T00:00:00Z" if status == "running" else stamp(1)},
            "HostConfig": {"Memory": memory_mb * 1024 ** 2}}


class FakeMachine:
    """Stands in for docker, df, du and crontab: saved answers, and a list of every command that ran.

    It starts like the bad-deploy fault: api 2.3.2 crash-loops, everything else runs.
    """

    def __init__(self) -> None:
        """The shop's 4 containers, empty logs, and no command failing yet."""
        self.ran = []
        self.fail = {}  # start of a command -> (exit code, output, error) to answer with instead
        self.containers = {
            "web": container("web", "nginx:1.27-alpine", memory_mb=256),
            "api": container("api", "lifeguard/real-api:2.3.2", status="restarting", exit_code=1, restarts=7),
            "db": container("db", "postgres:16-alpine"),
            "cache": container("cache", "redis:7.4-alpine", memory_mb=256),
        }
        self.logs = ("", "")  # (stdout, stderr) of docker logs

    def __call__(self, command: list[str], timeout=None, env=None) -> tuple[int, str, str]:
        """Answer one command like the real program would."""
        self.ran.append((command, env))
        for start, answer in self.fail.items():
            if " ".join(command).startswith(start):
                return answer
        if command[:2] == ["docker", "info"]:
            return 0, f"{4 * 1024 ** 3}\n", ""
        if command[:2] == ["docker", "inspect"]:
            found = [self.containers[name] for name in command[2:] if name in self.containers]
            missing = [name for name in command[2:] if name not in self.containers]
            return (1 if missing else 0), json.dumps(found), "".join(f"Error: No such object: {n}\n" for n in missing)
        if command[0] == "df":
            return 0, DF, ""
        if command[:2] == ["docker", "images"]:
            return 0, IMAGES, ""
        if command[:2] == ["docker", "logs"]:
            return 0, self.logs[0], self.logs[1]
        if command[:2] == ["docker", "stats"]:
            return 0, STATS, ""
        if command[0] == "crontab":
            return 1, "", "no crontab for ubuntu\n"
        if command[0] == "du":
            return 0, DU, "du: cannot read directory '/var/lib/docker/overlay2': Permission denied\n"
        return 0, "", ""  # docker restart, compose up, update: done, nothing to say

    def fixes(self) -> list[tuple[list[str], dict | None]]:
        """Only the commands that change something (restart, compose up, update)."""
        return [(command, env) for command, env in self.ran
                if command[:2] in (["docker", "restart"], ["docker", "compose"], ["docker", "update"])]


@pytest.fixture
def machine(monkeypatch) -> FakeMachine:
    """A fake machine in place of the real commands, for one test."""
    fake = FakeMachine()
    monkeypatch.setattr(docker_tools, "run", fake)
    return fake


def approved(*steps: dict) -> dict:
    """An approved decision with these steps, like approval.approve_option returns it."""
    return {"approved": True, "by": "pavan", "option": {"title": "test fix", "steps": list(steps)}}


ROLLBACK_API = {"type": "rollback", "container": "api", "to_image": "lifeguard/real-api:2.3.1"}
RESTART_CACHE = {"type": "restart", "container": "cache"}


def test_containers_look_like_docker_ps(machine):
    """The long real image name gets its own column, so 2.3.2 can't read like 2.3.27."""
    text = tools.list_containers(DockerServer())
    assert re.search(r"^api\s+lifeguard/real-api:2\.3\.2\s+7\s+Restarting \(1\) 1 minute ago$", text, re.MULTILINE)
    assert re.search(r"^db\s+postgres:16-alpine\s+0\s+Up 3 days$", text, re.MULTILINE)


def test_the_simulator_table_did_not_change():
    """Short image names keep the old 20-wide column, so the demo looks exactly as before."""
    assert tools.list_containers(Server()).splitlines()[0] == "NAME    IMAGE               RESTARTS  STATUS"


def test_logs_put_both_streams_back_in_time_order(machine):
    machine.logs = (f"{stamp(3)} INFO shop api 2.3.2 starting\n{stamp(1)} INFO shop api 2.3.2 starting\n",
                    f"{stamp(2)} FATAL config error: SHOP_DB_PASSWORD is not set (new in 2.3.2)\n")
    lines = docker_tools.get_logs(DockerServer(), "api", lines=20).splitlines()
    assert [line[20:] for line in lines] == ["INFO shop api 2.3.2 starting",
                                             "FATAL config error: SHOP_DB_PASSWORD is not set (new in 2.3.2)",
                                             "INFO shop api 2.3.2 starting"]
    # Local time in the simulator's format, so log lines and changes.log line up.
    assert re.match(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d ", lines[0])


def test_metrics_and_disks_use_the_simulator_columns(machine):
    server = DockerServer()
    metrics = docker_tools.get_metrics(server)
    assert re.search(r"^db\s+1\.5\s+46MiB / 512MiB\s+9%$", metrics, re.MULTILINE)
    # A container that isn't running uses nothing, and Docker's "--" for CPU counts as 0.
    assert re.search(r"^api\s+0\.0\s+0MiB / 512MiB", metrics, re.MULTILINE)
    assert re.search(r"^cache\s+0\.0\s+2MiB / 256MiB", metrics, re.MULTILINE)
    disks = docker_tools.check_disk(server)
    # Avail is what df says is available (16 GB), the boot partition is left out.
    assert re.search(r"^/\s+29\.4G\s+13\.4G\s+16\.0G\s+46%$", disks, re.MULTILINE)
    assert "/boot" not in disks


def test_folder_sizes_biggest_first(machine, monkeypatch):
    assert docker_tools.entries_to_measure("no/such/folder") == \
        "du: cannot access 'no/such/folder': No such file or directory"
    monkeypatch.setattr(docker_tools, "entries_to_measure", lambda path: ["/var/lib/docker", "/var/log", "/var/tmp"])
    text = docker_tools.check_folder_sizes(DockerServer(), "/var")
    assert text.splitlines() == ["12G     2026-10-26 11:00  /var/lib/docker",
                                 "1.0G    2026-10-26 12:00  /var/log",
                                 "4.0K    2026-01-01 10:00  /var/tmp",
                                 "13G                       total",
                                 "(1 folder could not be read: permission denied)"]


def test_changes_log_is_the_deploy_history(machine):
    docker_tools.CHANGES_LOG.write_text("2026-10-26 10:00:00 Started the shop\n"
                                        "a note without a time\n"
                                        "2026-10-26 11:00:00 Deployed api 2.3.2 (lifeguard/real-api:2.3.2, was 2.3.1)\n",
                                        encoding="utf-8")
    lines = docker_tools.recent_changes(DockerServer()).splitlines()
    assert len(lines) == 2 and "Deployed api 2.3.2" in lines[0]  # newest first
    assert lines[0].startswith("2026-10-26 11:00:00 (")


def test_jobs_and_health_checks(machine, monkeypatch):
    server = DockerServer()
    assert docker_tools.list_jobs(server) == "no crontab for ubuntu"
    answers = {"http://localhost/": (200, 4, None), "http://localhost/api/health": (502, 3, None)}
    monkeypatch.setattr(docker_tools, "http_get", lambda url, timeout=None: answers[url])
    assert docker_tools.check_endpoint(server, "http://localhost/api/health") == "HTTP 502 in 3 ms"
    assert docker_tools.check_endpoint(server, "http://example.com/").startswith("Error: unknown URL")
    assert make_alert() == "Uptime check failed: http://localhost/api/health answers HTTP 502."
    answers["http://localhost/api/health"] = (200, 5, None)
    assert make_alert() is None


def test_the_agent_gets_the_real_tools(machine):
    server = DockerServer()
    assert "lifeguard/real-api:2.3.2" in tools.run_tool(server, "list_containers", "{}")
    # The memory doesn't touch the server, so asking it runs no command.
    before = len(machine.ran)
    tools.run_tool(server, "search_past_incidents", '{"query": "SHOP_DB_PASSWORD is not set"}')
    assert len(machine.ran) == before


def test_a_docker_problem_is_text_not_a_crash(machine):
    server = DockerServer()
    machine.fail["docker inspect"] = (1, "", "Cannot connect to the Docker daemon. Is the docker daemon running?")
    assert tools.run_tool(server, "list_containers", "{}").startswith("Error: Cannot connect to the Docker daemon")


def test_clear_message_without_docker_or_without_the_shop(machine):
    machine.containers = {}
    with pytest.raises(DockerError, match="Start the shop first"):
        DockerServer()
    machine.fail["docker info"] = (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.")
    with pytest.raises(DockerError, match="Docker is not reachable"):
        DockerServer()


def test_safety_check_knows_the_real_menu(machine):
    options = [
        {"title": "Roll back api to 2.3.1", "steps": [ROLLBACK_API]},
        {"title": "Roll back api to 2.2.0", "steps": [{**ROLLBACK_API, "to_image": "lifeguard/real-api:2.2.0"}]},
        {"title": "Move the old logs", "steps": [{"type": "move_files", "from_path": "/var/log/shop", "to_mount": "/"}]},
        {"title": "Turn off the export", "steps": [{"type": "disable_job", "job": "export"}]},
        {"title": "Restart cache", "steps": [RESTART_CACHE]},
    ]
    safe, blocked = check_options(DockerServer(), options)
    assert [option["title"] for option in safe] == ["Roll back api to 2.3.1", "Restart cache"]
    reasons = {entry["title"]: entry["reason"] for entry in blocked}
    assert "not on this server" in reasons["Roll back api to 2.2.0"]
    assert "lifeguard/real-api:2.3.1" in reasons["Roll back api to 2.2.0"]  # it names the versions that are here
    assert "turned off in real Docker mode" in reasons["Move the old logs"]
    assert "turned off in real Docker mode" in reasons["Turn off the export"]


def test_the_final_call_sees_the_real_images(machine):
    facts = inventory(DockerServer())
    assert "Allowed actions here: restart, rollback, set_memory_limit." in facts
    assert "lifeguard/real-api:2.3.1" in facts and "Scheduled jobs: none" in facts


def test_an_approved_fix_runs_the_real_commands(machine):
    server = DockerServer()
    done = docker_tools.apply_fix(server, approved(ROLLBACK_API, RESTART_CACHE))
    assert done == ["rolled back api to lifeguard/real-api:2.3.1", "restarted cache"]
    assert machine.fixes() == [
        (["docker", "compose", "--file", str(docker_tools.COMPOSE_FILE), "up", "--detach", "--no-deps", "api"],
         {"API_IMAGE": "lifeguard/real-api:2.3.1"}),
        (["docker", "restart", "cache"], None),
    ]
    log = docker_tools.CHANGES_LOG.read_text(encoding="utf-8")
    assert log.count("Lifeguard-Agent fix:") == 2 and "(approved by pavan)" in log


def test_no_approval_no_command(machine):
    server = DockerServer()
    assert docker_tools.apply_fix(server, {"approved": False, "option": None, "by": "pavan"}) == []
    assert machine.fixes() == []


def test_a_failed_step_stops_the_fix(machine):
    server = DockerServer()
    machine.fail["docker compose"] = (1, "", "no such service: api")
    done = docker_tools.apply_fix(server, approved(ROLLBACK_API, RESTART_CACHE))
    assert len(done) == 1 and done[0].startswith("FAILED:") and "no such service: api" in done[0]
    assert [command for command, _ in machine.fixes()][-1][:2] == ["docker", "compose"]  # no restart after it


def test_memory_limit_command_and_the_second_lock():
    command, env, line = docker_tools.fix_command({"type": "set_memory_limit", "container": "api", "memory_mb": 512})
    assert command == ["docker", "update", "--memory", "512m", "--memory-swap", "512m", "api"]
    # Even if something got past safety.py, real mode refuses actions it doesn't do.
    with pytest.raises(DockerError, match="not allowed in real Docker mode"):
        docker_tools.fix_command({"type": "move_files", "from_path": "/var/log", "to_mount": "/"})


def test_verify_says_fixed_only_when_the_shop_really_works(machine, monkeypatch):
    server = DockerServer()
    monkeypatch.setattr(docker_tools, "http_get", lambda url, timeout=None: (502, 3, None))
    paused = []
    result = docker_tools.verify(server, wait_seconds=9, steady_seconds=10, pause=paused.append)
    # 3 checks, 3 seconds apart, then it gives up: the api still crash-loops.
    assert result["verdict"] == "NOT FIXED" and paused == [3, 3]
    assert "NO  http://localhost/api/health answers HTTP 502" in result["users_ok"]
    assert "NO  api is restarting" in result["stays_fixed"]

    machine.containers["api"] = container("api", "lifeguard/real-api:2.3.1")
    monkeypatch.setattr(docker_tools, "http_get", lambda url, timeout=None: (200, 5, None))
    paused.clear()
    result = docker_tools.verify(server, wait_seconds=9, steady_seconds=10, pause=paused.append)
    assert result["verdict"] == "FIXED" and paused == [10]  # it still watched 10 more seconds


def test_verify_catches_a_restart_while_watching(machine, monkeypatch):
    server = DockerServer()
    machine.containers["api"] = container("api", "lifeguard/real-api:2.3.1", restarts=7)
    monkeypatch.setattr(docker_tools, "http_get", lambda url, timeout=None: (200, 5, None))

    def crash_while_we_wait(seconds):
        machine.containers["api"]["RestartCount"] += 1

    result = docker_tools.verify(server, wait_seconds=3, steady_seconds=10, pause=crash_while_we_wait)
    assert result["verdict"] == "FIXED FOR NOW"
    assert "NO  api is running, restarted 1 time while we watched" in result["stays_fixed"]


def test_verify_survives_a_docker_hiccup(machine, monkeypatch):
    """The fix already ran: if Docker doesn't answer once, the check still ends with a verdict, not a crash."""
    server = DockerServer()
    machine.fail["docker info"] = (1, "", "Cannot connect to the Docker daemon")
    monkeypatch.setattr(docker_tools, "http_get", lambda url, timeout=None: (200, 5, None))
    result = docker_tools.verify(server, wait_seconds=3, steady_seconds=0, pause=lambda seconds: None)
    # Users are fine, but the last reading still shows the api restarting, so it can't say FIXED.
    assert result["verdict"] == "FIXED FOR NOW"


def test_sizes_and_times():
    assert docker_tools.to_mb("1.5GiB") == 1536 and docker_tools.to_mb("512KiB") == 0.5
    assert docker_tools.to_mb("0B") == 0 and docker_tools.to_mb("--") == 0
    assert docker_tools.human_size(4096) == "4.0K" and docker_tools.human_size(120 * 1024 ** 2) == "120M"
    assert docker_tools.docker_time("0001-01-01T00:00:00Z") is None
    assert docker_tools.docker_time("not a time") is None

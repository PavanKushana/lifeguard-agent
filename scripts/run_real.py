"""
run_real.py
Real Docker mode in the terminal: Lifeguard-Agent investigates the real shop on this server,
you approve a fix, and the fix runs as a real docker command. Then the shop is checked for real.

The same agent, prompts, safety check and approval as the demo. Only the server is real:
the tools come from docker_tools.py, and so do the fixes (restart, rollback, memory limit).
Run it on the server with the shop (see real-docker/README.md), from the project folder:
    bash real-docker/break.sh bad-deploy             break the shop first (or cache-crash, low-memory)
    python -m scripts.run_real                       the alert comes from the shop's own health checks
    python -m scripts.run_real "Customers can't log in"    or say what users see, in your words

There is no hidden truth here: the verdict comes from the live checks. The run and the
postmortem are saved like any other run (results/runs/ and results/incidents/).
The agent's memory here holds only this server's own incidents (real-docker/past_incidents.json):
a fix that worked is added to it, and the simulated demo's incidents are left out.
"""

import sys
from datetime import datetime

from app import docker_tools, memory
from app.agent import handle_incident
from app.docker_tools import DockerError, DockerServer
from app.postmortem import build_postmortem, save_postmortem
from scripts.run_incident import ask_human, print_step, save_run, say, step_times

# How long to wait for the shop to come back after a fix. Docker waits up to a minute between
# restarts of a crash-looping container, so less than that could miss a fix that worked.
WAIT_SECONDS = 75
# This server's own memory of past incidents. Not in git: every server builds its own.
REAL_MEMORY = docker_tools.REAL_DOCKER / "past_incidents.json"


def make_alert() -> str | None:
    """Build the alert from the shop's health checks, like an uptime monitor would. None if all is fine."""
    broken = []
    for url in docker_tools.URLS:
        status, _, error = docker_tools.http_get(url)
        if status != 200:
            broken.append(f"{url} answers HTTP {status}" if status else f"{url} doesn't answer ({error})")
    if not broken:
        return None
    return "Uptime check failed: " + "; ".join(broken) + "."


def fix_and_verify(server: DockerServer, decision: dict | None) -> tuple[list, dict]:
    """Run the approved fix for real (if any), show the server afterwards, then always give the verdict.

    Returns (the steps that ran, the verification result), for the postmortem.
    """
    approved = bool(decision and decision["approved"])
    done = []
    if approved:
        done = docker_tools.apply_fix(server, decision)
        print_step("apply", "\n".join(f"{number}. {line}" for number, line in enumerate(done, start=1)))
        say(f"\nChecking the shop for real (up to {WAIT_SECONDS} seconds)...")

    # Nothing changed without an approval, so one look is enough then.
    result = docker_tools.verify(server, wait_seconds=WAIT_SECONDS if approved else 0,
                                 steady_seconds=10 if approved else 0)

    if approved:
        after = [("docker ps -a", docker_tools.list_containers(server)),
                 ("curl http://localhost/api/health",
                  docker_tools.check_endpoint(server, "http://localhost/api/health")),
                 ("docker logs --tail 3 api", docker_tools.get_logs(server, "api", lines=3))]
        print_step("after", "\n\n".join(f"$ {command}\n{output}" for command, output in after))

    lines = [result["verdict"]]
    if approved and result["verdict"] != "NOT FIXED":
        lines.append("(Added to this server's memory of past incidents.)")
    if not approved:
        lines.append("No fix was approved, so nothing was changed.")
    lines += ["", "Users OK?"] + [f"  {line}" for line in result["users_ok"]]
    lines += ["Stays fixed?"] + [f"  {line}" for line in result["stays_fixed"]]
    if result["warnings"]:
        lines += ["Watch out:"] + [f"  {line}" for line in result["warnings"]]
    print_step("verdict", "\n".join(lines))
    return done, result


def remember(started: datetime, alert: str, final: dict | None, decision: dict | None, verdict: str) -> None:
    """A fix that worked goes into this server's memory, like on the demo (incidents.remember_incident).

    It keeps what the team learned (the agent's diagnosis and the approved fix). NOT FIXED isn't kept:
    a fix that didn't work is no hint for next time.
    """
    if not final or not decision or not decision["approved"] or verdict == "NOT FIXED":
        return
    memory.remember({"when": f"{started:%Y-%m-%d %H:%M}", "alert": alert,
                     "root_cause": str(final.get("root_cause", "")), "evidence": list(final.get("evidence", []))[:4],
                     "fix": str(decision["option"].get("title", "")), "result": verdict, "follow_up": "",
                     "source": "real server"})


def main() -> None:
    """Read the real server, run the agent on the alert, let a human decide, then check the shop for real."""
    started = datetime.now()
    try:
        server = DockerServer()
    except DockerError as error:
        print(f"Can't read the real server: {error}")
        return

    alert = " ".join(sys.argv[1:]).strip() or make_alert()
    if alert is None:
        print("The shop looks healthy: both health checks answer HTTP 200.\n"
              "Break it first, for example: bash real-docker/break.sh bad-deploy")
        return
    say(f"ALERT: {alert}")
    say("MODE: real Docker on this server. Fixes are real docker commands and only run after your yes.")

    # Only this server's own past incidents, never the simulated demo's (see memory.switch_to).
    memory.switch_to(REAL_MEMORY)
    result = handle_incident(server, alert, print_step)
    # The investigation took a minute; the approval checks the server as it is now.
    try:
        server.refresh()
    except DockerError as error:
        say(f"Couldn't read the server again ({error}); the approval checks the last reading.")
    decision = ask_human(server, result["final"])
    done, verification = fix_and_verify(server, decision)
    remember(started, alert, result["final"], decision, verification["verdict"])

    postmortem = build_postmortem({**result, "alert": alert, "started": started, "times": step_times,
                                   "decision": decision, "done": done, "verification": verification})
    postmortem_path = save_postmortem(postmortem, "real", started)
    say(f"\nCOST: {result['usage'].summary()}")
    print(f"\nSaved to {save_run('real', started)}")
    print(f"Postmortem: {postmortem_path}")


if __name__ == "__main__":
    main()

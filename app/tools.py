"""
tools.py
The commands the agent can run on the fake server, made to look like real Docker and Linux output.

Each tool reads the Server from simulator.py and returns plain text, exactly what
an engineer would see in a terminal. The agent never knows the server is fake:
to support a real server later, only this file needs a second version.
Every tool here only READS. Nothing in this file changes the server.
One tool, search_web, looks things up on the internet instead (code in web_search.py).

At the bottom: the tool menu the model reads (TOOL_SCHEMAS) and run_tool(),
which runs whatever the model asks for, safely.
"""

import json
from datetime import datetime

from app.simulator import Server
from app.web_search import search_web


def time_since(moment: datetime) -> str:
    """Turn a past time into Docker-style text like '3 days' or '20 seconds'."""
    seconds = int((datetime.now() - moment).total_seconds())
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            amount = seconds // size
            return f"{amount} {unit}{'s' if amount > 1 else ''}"
    return f"{seconds} seconds"


def gb(value: float) -> str:
    """Format a size the way 'df -h' does, e.g. 18.4G."""
    return f"{value:.1f}G"


def list_containers(server: Server) -> str:
    """Like 'docker ps -a': every container, its image, status and how often it restarted."""
    lines = [f"{'NAME':<8}{'IMAGE':<20}{'RESTARTS':<10}STATUS"]
    for c in server.containers.values():
        if c.status == "running":
            status = f"Up {time_since(c.since)}"
        elif c.status == "restarting":
            status = f"Restarting ({c.exit_code}) {time_since(c.since)} ago"
        else:
            status = f"Exited ({c.exit_code}) {time_since(c.since)} ago"
        lines.append(f"{c.name:<8}{c.image:<20}{c.restarts:<10}{status}")
    return "\n".join(lines)


def get_logs(server: Server, container: str, lines: int = 20) -> str:
    """Like 'docker logs --timestamps --tail N': the newest log lines of one container."""
    if container not in server.containers:
        return f"Error: No such container: {container}"
    newest = sorted(server.containers[container].logs)[-lines:]
    return "\n".join(f"{t:%Y-%m-%d %H:%M:%S} {text}" for t, text in newest)


def get_metrics(server: Server) -> str:
    """Like 'docker stats --no-stream': CPU and memory use of every container."""
    lines = [f"{'NAME':<8}{'CPU %':<8}{'MEM USAGE / LIMIT':<22}MEM %"]
    for c in server.containers.values():
        # A container that isn't running uses nothing, same as in real docker stats.
        cpu, mem = (c.cpu_percent, c.memory_mb) if c.status == "running" else (0.0, 0)
        usage = f"{mem}MiB / {c.memory_limit_mb}MiB"
        lines.append(f"{c.name:<8}{cpu:<8.1f}{usage:<22}{mem / c.memory_limit_mb:.0%}")
    return "\n".join(lines)


def check_disk(server: Server) -> str:
    """Like 'df -h': size, used and free space of every disk."""
    lines = [f"{'Mounted on':<22}{'Size':<8}{'Used':<8}{'Avail':<8}Use%"]
    for d in server.disks.values():
        avail = d.size_gb - d.used_gb
        lines.append(f"{d.mount:<22}{gb(d.size_gb):<8}{gb(d.used_gb):<8}{gb(avail):<8}"
                     f"{d.used_gb / d.size_gb:.0%}")
    return "\n".join(lines)


def recent_changes(server: Server) -> str:
    """Deploys and config changes, newest first. 'What changed?' is the first incident question."""
    newest_first = sorted(server.changes, reverse=True)
    # Clock time like the logs use, so changes and log lines are easy to line up.
    return "\n".join(f"{t:%Y-%m-%d %H:%M:%S} ({time_since(t)} ago) {text}" for t, text in newest_first)


def check_endpoint(server: Server, url: str) -> str:
    """Like 'curl': call a URL and report the HTTP status and response time."""
    if url not in server.endpoints:
        known = ", ".join(server.endpoints)
        return f"Error: unknown URL {url}. Known URLs: {known}"
    result = server.endpoints[url]
    # Status 0 is our way of saying nothing answered at all.
    if result["status"] == 0:
        return f"curl: (7) Failed to connect to {url}: Connection refused"
    return f"HTTP {result['status']} in {result['ms']} ms"


def web_search_tool(server: Server, query: str) -> str:
    """Look up an error on the internet. The server isn't used: the web is the same for every server."""
    return search_web(query)


# The 4 containers in simulator.py, listed so the model can only pick a real one.
CONTAINER_NAMES = ["web", "api", "db", "cache"]


def _tool(name: str, description: str, properties: dict | None = None,
          required: list | None = None) -> dict:
    """Build one menu entry in the format the OpenAI-style API expects."""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties or {},
                           "required": required or []},
        },
    }


# The tool menu the model reads. It only sees these names and descriptions, never the code above.
# None of them change anything; fixes come later and always need a human's approval.
TOOL_SCHEMAS = [
    _tool("list_containers", "Like 'docker ps -a': every container with its image, restart count and status."),
    _tool("get_logs", "Like 'docker logs --tail N': the newest log lines of one container, with timestamps.",
          {"container": {"type": "string", "enum": CONTAINER_NAMES},
           "lines": {"type": "integer", "description": "How many of the newest lines (default 20, max 50)."}},
          required=["container"]),
    _tool("get_metrics", "Like 'docker stats': CPU and memory use of every container, with memory limits."),
    _tool("check_disk", "Like 'df -h': size, used and free space of every disk."),
    _tool("recent_changes", "Deploys and config changes on this server, newest first, with times."),
    _tool("check_endpoint", "Like 'curl': call a URL and get the HTTP status and response time.",
          {"url": {"type": "string",
                   "enum": ["http://localhost/", "http://localhost/api/health"]}},
          required=["url"]),
    _tool("search_web", "Search the internet for an error message. Returns the top 3 pages with title, "
          "link and a short text. Search the error text without details that only exist on this server "
          "(timestamps, process IDs, file numbers, user names).",
          {"query": {"type": "string", "description": "The error message or question to search for."}},
          required=["query"]),
]

# Tool name -> the function that runs it. Only names in here can ever run.
_FUNCTIONS = {
    "list_containers": list_containers,
    "get_logs": get_logs,
    "get_metrics": get_metrics,
    "check_disk": check_disk,
    "recent_changes": recent_changes,
    "check_endpoint": check_endpoint,
    "search_web": web_search_tool,
}


def run_tool(server: Server, name: str, arguments: str) -> str:
    """Run one tool the model asked for and return its output as text.

    Anything unexpected (unknown tool, broken arguments) comes back as an error message
    the model can read and correct, instead of crashing the agent.
    """
    if name not in _FUNCTIONS:
        return f"Error: unknown tool '{name}'. Available tools: {', '.join(_FUNCTIONS)}"
    try:
        args = json.loads(arguments or "{}")
        if name == "get_logs":
            # Keep the answer short enough for the model: between 1 and 50 lines.
            args["lines"] = max(1, min(int(args.get("lines", 20)), 50))
        return _FUNCTIONS[name](server, **args)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        return f"Error: bad arguments for {name}: {error}"

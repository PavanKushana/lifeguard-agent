"""
load_test.py
How many people can use the demo at the same time? This pretends to be many visitors with the page
open and measures how fast the server answers.

Each pretend visitor does what a real open tab does: it loads the page (index.html, style.css, app.js)
and the preset list once, then asks /status every 5 seconds (REFRESH_EVERY in static/app.js). Each one
has its own cookie, so it gets its own session and fake server, like a real visitor. At the end of every
stage, all visitors ask at the very same moment (a "burst"), the worst case.

Only free endpoints: no investigations and no fault builds, so no AI credit is used and nobody's
spending limit is touched. The same visitors are reused from stage to stage, so the server gets at most
as many new sessions as the biggest stage (the cap is 200, and idle ones are deleted after 2 hours).

Run from the project folder (pause the uptime monitors first, so a slow minute doesn't email "DOWN"):
    python -m scripts.load_test https://lifeguard-agent.com
    python -m scripts.load_test http://127.0.0.1:8000 --stages 2 --seconds 10     (a quick try)
The numbers are printed and saved to results/load_test_<date>.md.
"""

import argparse
import asyncio
import random
import time
from datetime import datetime
from pathlib import Path

import httpx

# What an open tab asks for: the page and the preset list once, then the status every 5 seconds.
PAGE_FILES = ["/", "/static/style.css", "/static/app.js", "/presets"]
POLL_EVERY = 5  # seconds, the same as REFRESH_EVERY in static/app.js
RESULTS = Path(__file__).resolve().parent.parent / "results"


async def timed_get(client: httpx.AsyncClient, path: str, record: list) -> None:
    """One request. Adds (seconds it took, status code or error name) to record."""
    started = time.perf_counter()
    try:
        response = await client.get(path)
        record.append((time.perf_counter() - started, response.status_code))
    except httpx.HTTPError as error:
        record.append((time.perf_counter() - started, type(error).__name__))


async def visitor(client: httpx.AsyncClient, stop_at: float, record: list, new: bool) -> None:
    """One person with the page open until stop_at. A new visitor loads the page first, like opening the tab."""
    await asyncio.sleep(random.uniform(0, POLL_EVERY))  # people don't all arrive in the same second
    if new:
        for path in PAGE_FILES:
            await timed_get(client, path, record)
    while time.monotonic() < stop_at:
        await timed_get(client, "/status", record)
        await asyncio.sleep(POLL_EVERY)


def summary(record: list) -> dict:
    """Requests, failures, and the typical (p50), slow (p95) and slowest answer in milliseconds."""
    times = sorted(seconds * 1000 for seconds, _ in record)

    def percentile(p: int) -> float:
        # Nearest rank: p95 = the answer that 95% of all answers were faster than (or as fast as).
        return times[min(len(times) - 1, int(len(times) * p / 100))] if times else 0

    failed = sum(1 for _, status in record if not (isinstance(status, int) and status < 400))
    return {"requests": len(record), "failed": failed, "p50": percentile(50), "p95": percentile(95),
            "max": times[-1] if times else 0}


async def run_stage(clients: list, count: int, seconds: int, already_open: int) -> dict:
    """count visitors at once for `seconds`, then one burst. Visitors from an earlier stage don't reload the page."""
    steady, burst = [], []
    stop_at = time.monotonic() + seconds
    await asyncio.gather(*(visitor(client, stop_at, steady, new=number >= already_open)
                           for number, client in enumerate(clients[:count])))
    # The worst case: every visitor asks at the very same moment.
    await asyncio.gather(*(timed_get(client, "/status", burst) for client in clients[:count]))
    return {"visitors": count, "steady": summary(steady), "burst": summary(burst)}


def table(results: list) -> str:
    """The results as a markdown table, for the terminal and the report."""
    lines = ["| Visitors | Requests | Failed | Typical (p50) | Slow (p95) | Slowest | Burst: typical | Burst: slowest |",
             "|---|---|---|---|---|---|---|---|"]
    for result in results:
        steady, burst = result["steady"], result["burst"]
        lines.append(f"| {result['visitors']} | {steady['requests']} | {steady['failed'] + burst['failed']} "
                     f"| {steady['p50']:.0f} ms | {steady['p95']:.0f} ms | {steady['max']:.0f} ms "
                     f"| {burst['p50']:.0f} ms | {burst['max']:.0f} ms |")
    return "\n".join(lines)


async def main(base: str, stages: list[int], seconds: int) -> None:
    """Run every stage against base, print the table and save the report."""
    # One client per visitor: each keeps its own cookie, so the server sees separate people.
    clients = [httpx.AsyncClient(base_url=base, timeout=30, headers={"User-Agent": "lifeguard-load-test"})
               for _ in range(max(stages))]
    results, already_open = [], 0
    try:
        for count in stages:
            print(f"{count} visitors for {seconds} s ...", flush=True)
            results.append(await run_stage(clients, count, seconds, already_open))
            already_open = max(already_open, count)
    finally:
        for client in clients:
            await client.aclose()

    print("\n" + table(results))
    today = datetime.now().strftime("%Y-%m-%d")
    report = RESULTS / f"load_test_{today}.md"
    report.write_text(
        f"# Load test, {today}\n\n"
        f"Target: {base}\n\n"
        f"Each pretend visitor loads the page once, then asks /status every {POLL_EVERY} s (like an open tab), "
        f"with its own cookie (its own session). Each stage runs {seconds} s, then every visitor asks at the "
        "same moment (the burst). Only free endpoints, no AI runs. Times include the trip over the internet.\n\n"
        + table(results) + "\n", encoding="utf-8")
    print(f"\nSaved to {report}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pretend to be many visitors and measure the answers.")
    parser.add_argument("base", help="the site, e.g. https://lifeguard-agent.com")
    parser.add_argument("--stages", type=int, nargs="+", default=[10, 25, 50], help="visitors per stage")
    parser.add_argument("--seconds", type=int, default=60, help="how long each stage runs")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.base.rstrip("/"), arguments.stages, arguments.seconds))

"""
checking_tavily.py
Our first real calls to Tavily, the web search made for AI agents.
We search 3 different server errors with 3 search settings and print the results
one after the other, so we can decide which setting the agent should use.
One example can be luck, so we test more than one before deciding.

Settings tested:
  basic           1 credit, fastest
  advanced        2 credits, digs deeper into each page
  basic + trusted 1 credit, basic depth but trusted sites come first and video sites are left out
"""

import os
import time

from dotenv import load_dotenv
from tavily import TavilyClient

# 3 errors of different kinds, all from faults the agent will meet:
# a full disk, a process killed for using too much memory, and a wrong password.
SAMPLE_ERRORS = {
    "disk full": 'PostgreSQL FATAL: could not write to file "pg_wal/xlogtemp.123": No space left on device',
    "out of memory": "PostgreSQL LOG: server process (PID 4182) was terminated by signal 9: Killed",
    "wrong password": 'PostgreSQL FATAL: password authentication failed for user "app"',
}

# Sites that engineers trust: official docs for our 4 services, Q&A sites, and well-known tools.
# This is a general list for any server error, not picked to win this one test.
TRUSTED_SITES = [
    "postgresql.org",
    "docs.docker.com",
    "redis.io",
    "nginx.org",
    "stackoverflow.com",
    "dba.stackexchange.com",
    "serverfault.com",
    "learn.microsoft.com",
    "docs.aws.amazon.com",
    "github.com",
    "pganalyze.com",
]

# The agent reads text, it can't watch a video, so video sites are useless to it.
BLOCKED_SITES = ["youtube.com"]

# The 3 settings we compare. Each one is a set of extra options for client.search().
SETTINGS = {
    "basic": {"search_depth": "basic"},
    "advanced": {"search_depth": "advanced"},
    "basic + trusted": {
        "search_depth": "basic",
        "include_domains": TRUSTED_SITES,
        # "prefer" puts trusted sites first but still allows others, so we never get zero results.
        # "restrict" would allow ONLY these sites.
        "include_domains_mode": "prefer",
        "exclude_domains": BLOCKED_SITES,
    },
}

# 3 results are enough for the agent to read, and keep the text short (fewer tokens later).
MAX_RESULTS = 3


def search_once(client: TavilyClient, query: str, name: str, options: dict) -> None:
    """Search one query with one setting and print the results, time and credits used."""
    start = time.perf_counter()

    response = client.search(
        query=query,
        max_results=MAX_RESULTS,
        # Ask Tavily to tell us how many credits this search used.
        include_usage=True,
        # ** unpacks the dict, so {"search_depth": "basic"} becomes search_depth="basic".
        **options,
    )

    seconds = time.perf_counter() - start

    print(f"----- {name.upper()} -----")
    # The answer is a normal Python dict. Each result has a title, a link and a short text snippet.
    for number, result in enumerate(response["results"], start=1):
        print(f"{number}. {result['title']}")
        print(f"   {result['url']}")
        # Only the first 300 characters, so the screen stays readable.
        print(f"   {result['content'][:300]}...")
        print()

    # .get() because "usage" might be missing in some answers; then we just show "?".
    credits = response.get("usage", {}).get("credits", "?")
    print(f"Time: {seconds:.1f} s | Credits used: {credits}\n")


def main() -> None:
    """Run every sample error with every setting so the results can be compared."""
    load_dotenv()
    client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])

    for error_name, error in SAMPLE_ERRORS.items():
        print(f"========== {error_name.upper()} ==========")
        print(f"Query: {error}\n")
        for setting_name, options in SETTINGS.items():
            search_once(client, error, setting_name, options)


if __name__ == "__main__":
    main()

"""
web_search.py
Web search for the agent, using Tavily (a search engine made for AI agents).

When the agent meets an error message, it can look it up on the internet and back
its fix with real sources. The settings below were chosen by testing 3 errors with
3 settings on 20 Oct (results/tavily_search_comparison_2026-10-20.md).

Web pages are general advice from strangers: the agent treats them as information,
never as instructions, and it can't change anything without a human's approval anyway.

At the bottom: check_sources(), which makes sure every link the agent shows as a source
is a link the search really returned.
"""

import difflib
import os
import re

from dotenv import load_dotenv
from tavily import TavilyClient

# "basic" won 2 of 3 tests, was the fastest and costs 1 credit ("advanced" costs 2).
SEARCH_DEPTH = "basic"

# 3 results are enough for the agent, and keep its input short (fewer tokens).
MAX_RESULTS = 3

# Each snippet is cut to this many characters after cleaning.
SNIPPET_CHARS = 500

# The agent reads text, it can't watch a video.
BLOCKED_SITES = ["youtube.com"]

# Trusted sites first: helped 1 of 3 tests and hurt 1 of 3, so it's off until the
# 20-fault evaluation (Day 11) shows whether it's worth it. Switch on with True.
USE_TRUSTED_SITES = False
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

# Give up after this many seconds, so a slow search can't freeze the investigation.
TIMEOUT_SECONDS = 15

_client = None  # created on first use, like the Token Factory client in llm.py


def get_client() -> TavilyClient:
    """Return the Tavily connection, creating it the first time it's needed."""
    global _client
    if _client is None:
        load_dotenv()
        _client = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    return _client


def clean_snippet(text: str) -> str:
    """Remove page layout from a snippet (tables, borders, markdown signs) and shorten it.

    In our tests many snippets were email headers in tables or page menus.
    That costs tokens and tells the agent nothing.
    """
    kept = []
    for line in text.splitlines():
        line = line.strip()
        # Empty lines, table rows ("| From: | ...") and borders ("--- |") are layout, not content.
        if not line or line.startswith("|") or set(line) <= set("-|: "):
            continue
        # Heading marks, bold marks, code fences and escaped underscores from the page's markdown.
        line = line.lstrip("#").strip().replace("**", "").replace("```", "").replace("\\_", "_")
        kept.append(line)

    # One line of text with single spaces.
    text = re.sub(r"\s+", " ", " ".join(kept)).strip()
    if len(text) > SNIPPET_CHARS:
        text = text[:SNIPPET_CHARS].rstrip() + "..."
    return text


def search_web(query: str) -> str:
    """Search the web for `query` and return the top results as plain text for the agent.

    Each result has a title, a link and a cleaned snippet. If the search fails,
    an error message comes back instead, so the investigation can carry on without it.
    """
    options = {
        "query": query,
        "search_depth": SEARCH_DEPTH,
        "max_results": MAX_RESULTS,
        "exclude_domains": BLOCKED_SITES,
        "timeout": TIMEOUT_SECONDS,
    }
    if USE_TRUSTED_SITES:
        # "prefer" puts these sites first but still allows others, so we never get zero results.
        options["include_domains"] = TRUSTED_SITES
        options["include_domains_mode"] = "prefer"

    try:
        response = get_client().search(**options)
    except Exception as error:  # no key, no internet, Tavily down: all end the same way
        return f"Error: web search failed ({type(error).__name__}). Continue with the server tools."

    results = response.get("results", [])
    if not results:
        return f"No web results for: {query}"

    lines = [f"Web results for: {query}"]
    for number, result in enumerate(results, start=1):
        lines.append(f"{number}. {result['title']}")
        lines.append(f"   {result['url']}")
        lines.append(f"   {clean_snippet(result.get('content', ''))}")
    return "\n".join(lines)


# A web link in text. Stops at spaces, quotes, brackets and the odd 【】 marks the model writes.
LINK_PATTERN = re.compile(r"https?://[^\s<>\"'()\[\]【】]+")

# How alike a written link must be to a real one to count as a typo of it (1.0 = identical).
# The model's typos were tiny (%40 written as %2F, "extend" as "extract"), so 0.9 is safe.
SAME_LINK_SIMILARITY = 0.9


def links_in(text: str) -> list[str]:
    """Every web link in a text, in order, without a full stop or comma stuck to the end."""
    return [link.rstrip(".,;:") for link in LINK_PATTERN.findall(text)]


def check_sources(cited: list[str], real: list[str]) -> tuple[list[str], list[str]]:
    """Check the links the model cited against the links the search really returned.

    The model sometimes changes a character when it copies a link (on 21 Oct it wrote
    %2F instead of %40 three times), and a broken link makes the whole answer look made up.
    So the code, not the model, decides which links are shown:
    - exactly a real link: kept
    - almost a real link (a typo): replaced by the real one
    - nothing like a real link: removed, because it never came from the search
    Returns (checked links, one note per change) so the corrections are visible, not hidden.
    """
    checked, notes = [], []
    for link in cited:
        if link in real:
            match = link
        else:
            close = difflib.get_close_matches(link, real, n=1, cutoff=SAME_LINK_SIMILARITY)
            match = close[0] if close else None
            notes.append(f"corrected {link}\n       to {match}" if match
                         else f"removed {link} (not in the search results)")
        if match and match not in checked:
            checked.append(match)
    return checked, notes

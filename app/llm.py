"""
llm.py
The one place that talks to Nebius Token Factory.

Every model call in the project goes through ask(), so the connection, the model
names and the counting of time and tokens all live here. If the API address or a
model name changes, only this file needs to change.
"""

import os
import time
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"

# How often a failed call is tried again (busy, overloaded, network). The library waits longer
# each time: 0.5, 1, 2, 4, 8, 8 s, about 20 s in total. Its default of 2 retries gave up after
# about 1.3 s, while Ultra's busy spells (HTTP 503) lasted around 10 s (seen 24 Oct, lesson 48).
MAX_RETRIES = 6
# Answers that mean "busy, try again later": too many requests, or the service is overloaded.
BUSY_CODES = {429, 502, 503, 504}
# If Token Factory is still busy after those quick retries, wait this long and try again,
# for up to BUSY_PATIENCE seconds more. On 24 Oct it turned away even tiny Nano calls for
# longer than 20 s at times, while our own limits (100 requests, 800k tokens) were nearly unused:
# it sheds load when it's busy. A fault build runs behind a spinner and can wait that long.
BUSY_WAIT = 10
BUSY_PATIENCE = 60

# Which Nemotron model does which job, chosen on Day 2 after comparing them.
MODELS = {
    "triage": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "investigator": "nvidia/nemotron-3-super-120b-a12b",
    "final_call": "nvidia/Nemotron-3-Ultra-550b-a55b",
    # Building a custom fault is creative and must fit together, so it gets the strongest model too.
    "fault_maker": "nvidia/Nemotron-3-Ultra-550b-a55b",
}

_client = None  # created on first use, so files that never call a model don't need an API key


def get_client() -> OpenAI:
    """Return the Token Factory connection, creating it the first time it's needed."""
    global _client
    if _client is None:
        load_dotenv()
        _client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"],
                         max_retries=MAX_RETRIES)
    return _client


def describe_error(error: Exception) -> str:
    """Turn a failed model call into one short sentence a visitor understands.

    The openai library calls every 5xx answer "InternalServerError", and its message can be
    a whole HTML error page, so the status code is what tells us what really happened.
    """
    if isinstance(error, APIStatusError):
        if error.status_code in BUSY_CODES:
            return (f"Token Factory (the AI service) is busy right now (HTTP {error.status_code}), "
                    "even after several retries. Wait a few seconds and try again.")
        return f"Token Factory answered with an error (HTTP {error.status_code}). Please try again."
    # A timeout is a kind of connection error, so it's checked first.
    if isinstance(error, APITimeoutError):
        return "Token Factory took too long to answer. Please try again."
    if isinstance(error, APIConnectionError):
        return "Couldn't reach Token Factory (network problem). Check the connection and try again."
    return f"{type(error).__name__}: {error}"


@dataclass
class Usage:
    """Running totals for one incident, so we can show its time and token count at the end."""

    calls: int = 0
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    # Nemotron thinks before it answers (hidden "reasoning_content"). Those tokens are part of
    # tokens_out and count against max_tokens, so they're counted separately to see how much they take.
    tokens_thinking: int = 0
    # Why the newest reply ended: "stop" (finished) or "length" (cut off at max_tokens).
    # Kept so a broken reply can say whether it was cut off, instead of us guessing.
    last_finish: str = ""

    def summary(self) -> str:
        """One line like '5 model calls, 12.3 s, 4,210 tokens in, 1,180 out (620 of them thinking)'."""
        return (f"{self.calls} model calls, {self.seconds:.1f} s, "
                f"{self.tokens_in:,} tokens in, {self.tokens_out:,} out ({self.tokens_thinking:,} of them thinking)")


def create_with_patience(options: dict):
    """Send one request. If Token Factory stays busy after the library's quick retries,
    wait BUSY_WAIT seconds and try again, until BUSY_PATIENCE seconds have passed.
    Any other error (a wrong model name, a bad key) is raised at once: waiting won't fix it.
    """
    give_up_at = time.monotonic() + BUSY_PATIENCE
    while True:
        try:
            return get_client().chat.completions.create(**options)
        except APIStatusError as error:
            if error.status_code not in BUSY_CODES or time.monotonic() > give_up_at:
                raise
            time.sleep(BUSY_WAIT)


def ask(job: str, messages: list, usage: Usage, tools: list | None = None, max_tokens: int = 4000):
    """Send the conversation to the model for this job (a key of MODELS, e.g. 'triage' or 'final_call').

    Adds the time and tokens to `usage`, notes why the reply ended (usage.last_finish)
    and returns the model's reply message.
    max_tokens includes the model's hidden thinking, so it must leave room for both.
    It's only a ceiling: we pay for the tokens really written, not for the room.
    """
    options = {"model": MODELS[job], "messages": messages, "max_tokens": max_tokens}
    if tools:
        options["tools"] = tools

    start = time.perf_counter()
    response = create_with_patience(options)
    usage.seconds += time.perf_counter() - start  # includes any waiting while Token Factory was busy

    usage.calls += 1
    if response.usage:  # token counts, which is what we pay for
        usage.tokens_in += response.usage.prompt_tokens
        usage.tokens_out += response.usage.completion_tokens
        details = response.usage.completion_tokens_details
        usage.tokens_thinking += (details.reasoning_tokens or 0) if details else 0
    usage.last_finish = response.choices[0].finish_reason or ""
    return response.choices[0].message

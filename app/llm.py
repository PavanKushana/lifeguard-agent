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
from openai import OpenAI

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"

# Which Nemotron model does which job, chosen on Day 2 after comparing them.
MODELS = {
    "triage": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "investigator": "nvidia/nemotron-3-super-120b-a12b",
    "final_call": "nvidia/Nemotron-3-Ultra-550b-a55b",
}

_client = None  # created on first use, so files that never call a model don't need an API key


def get_client() -> OpenAI:
    """Return the Token Factory connection, creating it the first time it's needed."""
    global _client
    if _client is None:
        load_dotenv()
        _client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])
    return _client


@dataclass
class Usage:
    """Running totals for one incident, so we can show its time and token count at the end."""

    calls: int = 0
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def summary(self) -> str:
        """One line like '5 model calls, 12.3 s, 4,210 tokens in, 1,180 out'."""
        return (f"{self.calls} model calls, {self.seconds:.1f} s, "
                f"{self.tokens_in:,} tokens in, {self.tokens_out:,} out")


def ask(job: str, messages: list, usage: Usage, tools: list | None = None, max_tokens: int = 1500):
    """Send the conversation to the model for this job ('triage', 'investigator' or 'final_call').

    Adds the time and tokens to `usage` and returns the model's reply message.
    """
    options = {"model": MODELS[job], "messages": messages, "max_tokens": max_tokens}
    if tools:
        options["tools"] = tools

    start = time.perf_counter()
    response = get_client().chat.completions.create(**options)
    usage.seconds += time.perf_counter() - start

    usage.calls += 1
    if response.usage:  # token counts, which is what we pay for
        usage.tokens_in += response.usage.prompt_tokens
        usage.tokens_out += response.usage.completion_tokens
    return response.choices[0].message

"""
comparing_models.py
Sends the same server error to several Nemotron models
and prints each answer with its time and token count,
so we can pick the right model for each job in Lifeguard-Agent.
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"

# The models we want to compare (exact names from list_models.py).
MODELS = [
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    "nvidia/Nemotron-3_5-Lightning",
    "nvidia/nemotron-3-super-120b-a12b",
    "nvidia/Nemotron-3-Ultra-550b-a55b",
]

SYSTEM_PROMPT = "You are a senior site reliability engineer. Answer in 3 short bullet points."

# Same disk-full error as yesterday, so the answers are easy to compare.
SAMPLE_LOG = 'FATAL: could not write to file "pg_wal/xlogtemp.123": No space left on device'


def ask_model(client: OpenAI, model: str) -> None:
    """
    Ask one model about SAMPLE_LOG and print its answer, time and tokens.

    Args:
        client: the connection to Token Factory
        model:  the exact model name to ask
    """
    start = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"What does this log line mean and what should I check first?\n\n{SAMPLE_LOG}"},
        ],
        max_tokens=1500,
    )
    seconds = time.perf_counter() - start

    # Some models "think" before answering and may return an empty answer
    # if they run out of tokens while thinking, so we handle that case.
    answer = response.choices[0].message.content or "(no answer text returned)"

    print("=" * 70)
    print(f"MODEL: {model}")
    print(f"Time: {seconds:.1f} s | Tokens: {response.usage.prompt_tokens} in, {response.usage.completion_tokens} out")
    print("-" * 70)
    print(answer.strip())


def main() -> None:
    """Ask every model in MODELS the same question, one after another."""
    load_dotenv()
    client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])

    for model in MODELS:
        # If one model fails (for example it's busy), print the error and carry on with the next.
        try:
            ask_model(client, model)
        except Exception as error:
            print("=" * 70)
            print(f"MODEL: {model} FAILED: {error}")


if __name__ == "__main__":
    main()

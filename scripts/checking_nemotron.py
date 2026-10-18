"""
checking_nemotron.py
Our first real call to a Nemotron model on Nebius Token Factory.
We send one server error line and ask the model what it means.
"""

import os
import time

from dotenv import load_dotenv
from openai import OpenAI

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"

# The model we test first: Super, the mid-size one (exact name from list_models.py).
MODEL = "nvidia/nemotron-3-super-120b-a12b"

# A realistic error from a database whose disk is full.
# Our agent will read lines like this every day.
SAMPLE_LOG = 'FATAL: could not write to file "pg_wal/xlogtemp.123": No space left on device'


def main() -> None:
    """Send one question to Nemotron and print the answer, the time taken and the tokens used."""
    load_dotenv()
    client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])

    # Start a timer so we know how fast the model answers.
    start = time.perf_counter()

    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            # "system" sets the model's role and how it should answer.
            {
                "role": "system",
                "content": "You are a senior site reliability engineer. Answer in 3 short bullet points.",
            },
            # "user" is the actual question.
            {
                "role": "user",
                "content": f"What does this database log line mean and what should I check first?\n\n{SAMPLE_LOG}",
            },
        ],
        # Upper limit for the answer length. This also limits the cost.
        max_tokens=1000,
    )

    seconds = time.perf_counter() - start

    # The model's answer is inside choices[0].message.content.
    print(response.choices[0].message.content)
    print(f"\nTime: {seconds:.1f} s")
    # "usage" shows how many tokens we paid for: input (our question) and output (the answer).
    print(f"Tokens: {response.usage.prompt_tokens} in, {response.usage.completion_tokens} out")


if __name__ == "__main__":
    main()

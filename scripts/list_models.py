"""
list_models.py
Prints the Nemotron models available on Nebius Token Factory,
so we know the exact model names to use in our code.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI

# Token Factory understands the same API format as OpenAI,
# so we use the official openai package and only change the address.
TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"


def main() -> None:
    """Connect to Token Factory and print every model whose name contains 'nemotron'."""
    # Read the .env file and put NEBIUS_API_KEY into the environment,
    # so the key never has to be written inside the code.
    load_dotenv()

    client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])

    # Ask for the full model list, then keep only the Nemotron ones.
    for model in client.models.list():
        if "nemotron" in model.id.lower():
            print(model.id)


# Run main() only when this file is started directly,
# not when another file imports it.
if __name__ == "__main__":
    main()
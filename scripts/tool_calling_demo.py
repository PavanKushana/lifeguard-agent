"""
tool_calling_demo.py
A tiny agent with one fake tool, to see how tool calling works.

The model can't look at a server itself. It can only ASK us to run a tool.
Our code runs the tool, sends the result back, and the model continues.
This repeats until the model gives a final answer. That loop is the "agent".
"""

import json
import os

from dotenv import load_dotenv
from openai import OpenAI

TOKEN_FACTORY_URL = "https://api.tokenfactory.nebius.com/v1/"
MODEL = "nvidia/nemotron-3-super-120b-a12b"

# Safety net: stop after this many rounds, even if the model keeps asking for tools.
MAX_STEPS = 5

# The tool description the model reads. It only sees this text, never our Python code.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_disk",
            "description": "Show how full the disks on the server are, like 'df -h'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Folder to check, e.g. /var/lib/postgresql",
                    }
                },
                "required": ["path"],
            },
        },
    }
]


def check_disk(path: str) -> str:
    """Fake version of 'df -h': always reports a full disk, so we know the right answer."""
    return (
        "Filesystem  Size  Used  Avail  Use%  Mounted on\n"
        f"/dev/sdb1    50G   50G     0   100%  {path}"
    )


def run_tool(name: str, arguments: str) -> str:
    """Run the tool the model asked for. Our code decides what actually runs, not the model."""
    args = json.loads(arguments)
    if name == "check_disk":
        return check_disk(args.get("path", "/"))
    # The model asked for something we never offered: refuse instead of guessing.
    return f"Error: unknown tool '{name}'"


def main() -> None:
    """Ask the model about a failing database and let it use check_disk until it answers."""
    load_dotenv()
    client = OpenAI(base_url=TOKEN_FACTORY_URL, api_key=os.environ["NEBIUS_API_KEY"])

    # The conversation so far. Each round we add to it, so the model sees everything.
    messages = [
        {
            "role": "system",
            "content": "You are a site reliability engineer. Use the tools to check "
            "before you answer. Answer in 3 short bullet points.",
        },
        {
            "role": "user",
            "content": "Our PostgreSQL database stopped accepting writes. "
            "Data is in /var/lib/postgresql. What is wrong?",
        },
    ]

    for step in range(1, MAX_STEPS + 1):
        print(f"\nStep {step}: asking the model...")
        response = client.chat.completions.create(
            model=MODEL, messages=messages, tools=TOOLS, max_tokens=1000
        )
        reply = response.choices[0].message

        # No tool request means the model is done: print its answer and stop.
        if not reply.tool_calls:
            print("  Final answer:\n")
            print(reply.content)
            return

        # Keep the model's request in the history, then answer each tool call.
        messages.append(reply)
        for call in reply.tool_calls:
            print(f"  Model asks for tool: {call.function.name}({call.function.arguments})")
            result = run_tool(call.function.name, call.function.arguments)
            print(f"  We ran it. Result:\n{result}")
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    print(f"\nStopped after {MAX_STEPS} steps without a final answer.")


if __name__ == "__main__":
    main()

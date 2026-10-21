"""
approval.py
The human's decision: pick one of Ultra's fix options and approve it, or reject them all.

This is the ONE place where a fix gets approved. The terminal now, and later the web page
and the Telegram bot, all call approve_option(), so the rules are the same everywhere:
- the human picks the option, the AI never picks for them
- the safety check runs again right before approval, because the server may have
  changed since the options were made
- every decision is recorded with who made it and when
"""

from datetime import datetime

from app.safety import check_steps
from app.simulator import Server


def approve_option(server: Server, final: dict, number: int, approved_by: str) -> dict:
    """Approve fix option `number` (1, 2, 3) from Ultra's final call, or reject all with 0.

    Returns a decision record:
    {"approved": True/False, "option": the option or None, "by": ..., "at": ..., "note": why}
    Nothing is changed on the server here; applying the fix is a separate step.
    """
    decision = {"approved": False, "option": None, "by": approved_by,
                "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    options = final.get("options", []) if final else []

    if number == 0:
        decision["note"] = "rejected all options, nothing will change"
        return decision
    if not 1 <= number <= len(options):
        decision["note"] = f"there is no option {number}"
        return decision

    option = options[number - 1]
    # Check again at the moment of approval, not only when the options were made.
    reason = check_steps(server, option.get("title", ""), option.get("steps"))
    if reason:
        decision["note"] = f"blocked by the safety check: {reason}"
        return decision

    decision.update(approved=True, option=option, note=f"approved option {number}: {option.get('title')}")
    return decision

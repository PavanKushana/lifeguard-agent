"""
telegram_bot.py
The pager: Telegram messages for the visitor's demo shop, and fixes approved from the phone.

After the visitor links their Telegram (a button on the page opens the bot with a one-time code):
- the shop breaks -> the agent starts by itself and the phone gets the alert (main.after_break)
- the agent is done -> the root cause and the fix options arrive, with an Approve button per option
- a tap on "Approve 2", then "Yes, apply" -> incidents.decide_incident(), the SAME code as the website
- the verdict and the postmortem come back to the chat (also after a decision on the website)

Telegram's Bot API is plain HTTPS: a POST to https://api.telegram.org/bot<token>/<method>.
How messages reach us: on the live server Telegram calls our /telegram/webhook (a "webhook");
on a laptop there's no public address, so a background thread asks Telegram for new messages
("long polling"). Each place has its own bot and token (live and Dev), because a bot can deliver
its messages to only one server: with one shared bot, testing would steal the live bot's messages.

Security: only the chat linked to a session can approve that session's fix; link codes are random,
used once and expire; the webhook checks a secret header only Telegram knows; and the token is part
of every Bot API address, so those addresses are never written to the log.
"""

import hashlib
import html
import io
import logging
import os
import secrets
import threading
import time
from datetime import datetime, timedelta

import httpx
import segno
from dotenv import load_dotenv

from app import incidents
from app.sessions import Incident, Session, find, find_all

# Read .env on a laptop (on the host the settings are real environment variables already).
load_dotenv()
# No token = Telegram off: the page hides the button and nothing here runs.
TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# The live site's address. Set (on Render): Telegram calls our webhook. Not set (laptop): long polling.
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").strip().rstrip("/")
API = f"https://api.telegram.org/bot{TOKEN}"
# How long a link from the page's button works. Short, because whoever has the link can link a chat.
LINK_CODE_LIFETIME = timedelta(minutes=15)
# The owner's chat (Pavan's): gets a short note whenever any visitor finishes an incident, so he sees
# judges using the demo. Optional. Find the number by sending /myid to the bot. In a private chat it's
# the person's Telegram id, so the same number works for the live bot and the Dev bot.
OWNER_CHAT_ID = int(os.environ["OWNER_CHAT_ID"]) if os.environ.get("OWNER_CHAT_ID", "").strip() else None

log = logging.getLogger("uvicorn.error")
# 40 s: long polling waits up to 30 s for news, so the connection must wait a little longer.
_client = httpx.Client(timeout=40)
_username: str | None = None  # the bot's @name, asked from Telegram once


def enabled() -> bool:
    """True when a bot token is set, so Telegram alerts can work."""
    return bool(TOKEN)


# ---------- Talking to Telegram ----------

def call(method: str, **params):
    """One Bot API call. Returns Telegram's "result", or None if it failed.

    A failed message is logged and dropped: a lost Telegram message must never break an incident.
    Only the method and the error type are logged, never the address, because it contains the token.
    """
    try:
        data = _client.post(f"{API}/{method}", json=params).json()
    except (httpx.HTTPError, ValueError) as error:
        log.warning("Telegram %s failed: %s", method, type(error).__name__)
        return None
    if not data.get("ok"):
        log.warning("Telegram %s failed: %s", method, data.get("description"))
        return None
    return data["result"]


def in_background(function, *args) -> None:
    """Run a Telegram call without making the visitor's request wait for it."""
    threading.Thread(target=function, args=args, daemon=True).start()


def e(text) -> str:
    """Text made safe for Telegram's HTML mode: <, > and & from logs or the AI can't break the message."""
    return html.escape(str(text), quote=False)


def send(chat_id: int, text: str, buttons: list | None = None) -> int | None:
    """Send a message (HTML), optionally with buttons under it. Returns its id, to edit it later."""
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    result = call("sendMessage", **params)
    return result["message_id"] if result else None


def edit(chat_id: int, message_id: int, text: str, buttons: list | None = None) -> None:
    """Change a message we sent. Without buttons, its old buttons disappear (so nobody taps them twice)."""
    params = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True}
    if buttons:
        params["reply_markup"] = {"inline_keyboard": buttons}
    call("editMessageText", **params)


def answer(query_id: str, text: str | None = None) -> None:
    """Confirm a button tap (the button stops spinning), with an optional short pop-up text."""
    params = {"callback_query_id": query_id}
    if text:
        params["text"] = text
    call("answerCallbackQuery", **params)


def send_document(chat_id: int, filename: str, content: str, caption: str) -> None:
    """Send a file, here the postmortem in markdown, so it can be read and kept on the phone."""
    try:
        data = _client.post(f"{API}/sendDocument", data={"chat_id": str(chat_id), "caption": caption},
                            files={"document": (filename, content.encode("utf-8"), "text/markdown")}).json()
        if not data.get("ok"):
            log.warning("Telegram sendDocument failed: %s", data.get("description"))
    except (httpx.HTTPError, ValueError) as error:
        log.warning("Telegram sendDocument failed: %s", type(error).__name__)


def bot_username() -> str | None:
    """The bot's @name (asked from Telegram once), for the link on the page."""
    global _username
    if _username is None and enabled():
        me = call("getMe")
        _username = me.get("username") if me else None
    return _username


def webhook_secret() -> str:
    """The secret Telegram sends with every webhook call, made from the token so there's nothing extra to store.

    Without it anyone who knows our address could send fake button taps to /telegram/webhook.
    """
    return hashlib.sha256(f"lifeguard-webhook:{TOKEN}".encode()).hexdigest()


def page_url() -> str:
    """Where the demo page is, for links in messages."""
    return PUBLIC_URL or "http://127.0.0.1:8000"


# ---------- Linking a chat to a visitor's session ----------

def new_link_code(session: Session) -> str:
    """A fresh one-time code for the page's "Get alerts on Telegram" link.

    Not the session id: that works like a password for the visitor's server (it's in their cookie).
    22 random characters (letters, digits, - and _: what Telegram allows in a start link).
    """
    session.telegram_code = secrets.token_urlsafe(16)
    session.telegram_code_until = datetime.now() + LINK_CODE_LIFETIME
    return session.telegram_code


def link_url(code: str) -> str:
    """t.me/<bot>?start=<code>: opens the bot, and pressing Start sends us "/start <code>"."""
    return f"https://t.me/{bot_username()}?start={code}"


def qr_svg(url: str) -> str:
    """The link as a QR code picture (SVG), for visitors on a laptop with Telegram only on their phone:
    they scan it with the phone camera and the bot opens there, code included (25 Oct: Pavan couldn't
    link from the laptop, because the t.me page's Start button needs the Telegram app on that computer)."""
    picture = io.BytesIO()
    # omitsize=True: no fixed width and height, only a viewBox, so the picture shrinks to the size the
    # page gives it. With a fixed size the page cut it off, two of the three corner squares were missing
    # and no phone could read it (25 Oct). make_qr: always a normal QR code, never the tiny "Micro QR"
    # kind that phone cameras don't read. border=4: the white margin around the code that scanners need.
    segno.make_qr(url, error="m").save(picture, kind="svg", border=4, omitsize=True, xmldecl=False,
                                       dark="#000000", light="#ffffff")
    return picture.getvalue().decode("utf-8")


def link(session: Session, chat_id: int) -> None:
    """Link this chat to this session. One chat follows one shop: an older link of the chat is removed."""
    for other in find_all(lambda s: s.telegram_chat_id == chat_id and s is not session):
        other.telegram_chat_id = None
    session.telegram_chat_id = chat_id
    session.telegram_code = None  # used once
    session.telegram_code_until = None


# ---------- The messages ----------

def alert_text(alert: str, refused: str | None = None) -> str:
    """The first buzz: what the customers report, and that the agent is on it."""
    text = f"🚨 <b>Your demo shop has a problem</b>\nCustomers report: \"{e(alert)}\"\n\n"
    if refused:
        return text + f"The agent can't start by itself right now: {e(refused)}"
    return text + "Lifeguard-Agent is investigating now. This takes about 30 seconds..."


def options_message(incident: Incident) -> tuple[str, list]:
    """The root cause and the fix options, with an Approve button per option and Reject all."""
    final = incidents.final_call_view(incident.result["final"])
    lines = [f"🔎 <b>Found the cause</b> ({e(final['confidence'])} confidence)", e(final["root_cause"]), ""]
    for option in final["options"]:
        undo = "can be undone" if option["can_be_undone"] else "can't be undone"
        star = "  ⭐ recommended" if option["recommended"] else ""
        lines.append(f"<b>{option['number']}. {e(option['title'])}</b>{star}\n    risk {e(option['risk'])}, {undo}")
    blocked = len(final["blocked"])
    if blocked:
        lines.append(f"\n🛡 {blocked} more option{' was' if blocked == 1 else 's were'} blocked by the safety check.")
    if not final["options"]:
        lines.append("No safe option is left: reject, and fix it by hand.")
    lines.append(f"\nPick a fix, or see every detail on the page: {e(page_url())}")
    buttons = [[{"text": f"Approve {option['number']}", "callback_data": f"pick:{incident.id}:{option['number']}"}]
               for option in final["options"]]
    buttons.append([{"text": "Reject all", "callback_data": f"pick:{incident.id}:0"}])
    return "\n".join(lines), buttons


def confirm_message(incident: Incident, number: int) -> tuple[str, list]:
    """The second tap, like the website's "Click again to apply": picking alone is not approving."""
    if number == 0:
        text = "<b>Reject all options?</b>\nNothing on the server changes, and the customers stay affected."
        yes = "Yes, reject all"
    else:
        option = incidents.final_call_view(incident.result["final"])["options"][number - 1]
        steps = "\n".join(f"{step_number}. {e(step)}" for step_number, step in enumerate(option["steps"], 1))
        text = (f"<b>Apply option {number}: {e(option['title'])}?</b>\n{steps}\n\n"
                f"This changes your server. Undo: {e(option['undo'])}")
        yes = "✅ Yes, apply"
    buttons = [[{"text": yes, "callback_data": f"yes:{incident.id}:{number}"},
                {"text": "Cancel", "callback_data": f"back:{incident.id}:0"}]]
    return text, buttons


def verdict_text(incident: Incident) -> str:
    """How it ended, in the same words as the website (incidents.outcome)."""
    verdict = incident.verification["verdict"]
    icon = {"FIXED": "✅", "FIXED FOR NOW": "🟡", "NOT FIXED": "❌"}.get(verdict, "")
    result = incidents.outcome(incident)
    lines = [f"{icon} <b>{e(verdict)}</b>",
             f"Decision: {e(incident.decision['note'])} (from {e(incident.decision['by'])})"]
    if verdict == "NOT FIXED":
        lines.append(f"Customers still report: \"{e(incident.alert)}\"")
    lines += [e(text) for text in (result["back"], result["kept"], result["still"]) if text]
    if result["watch"]:
        lines.append(f"⚠️ {e(result['watch'])}")
    proof = [line["text"] for line in incidents.proof_lines(incident.proof) if line["success"]]
    if proof:
        lines.append(f"Proof from the server: {e(proof[0])}")
    return "\n".join(lines)


# ---------- What happens, from incidents.py ----------

def send_alert(session: Session, refused: str | None = None) -> None:
    """Called by main.py when a linked visitor's shop breaks (refused = why the agent couldn't start)."""
    if session.telegram_chat_id is not None:
        in_background(send, session.telegram_chat_id, alert_text(session.fault["alert"], refused))


def on_options(session: Session, incident: Incident) -> None:
    """The agent is done: send the root cause and the fix options to the linked chat."""
    if session.telegram_chat_id is not None:
        text, buttons = options_message(incident)
        send(session.telegram_chat_id, text, buttons)


def on_failed(session: Session, incident: Incident) -> None:
    """The agent stopped with an error: say so, so nobody waits for options that won't come."""
    if session.telegram_chat_id is not None:
        reason = incident.error or "Ultra's answer couldn't be used."
        send(session.telegram_chat_id, f"❌ The agent stopped: {e(reason)}\nReset the shop on the page and try again.")


def on_decided(session: Session, incident: Incident) -> None:
    """A decision was made (on the phone or the website): send the verdict and the postmortem."""
    chat_id = session.telegram_chat_id
    if chat_id is None:
        return

    def deliver():
        send(chat_id, verdict_text(incident))
        try:
            text, filename = incidents.postmortem(session)
            send_document(chat_id, filename, text, "The postmortem: what happened, why, and what was done.")
        except incidents.IncidentError:
            pass  # a reset in the same second: there's nothing to report any more

    in_background(deliver)


def problem_name(session: Session) -> str:
    """What the visitor broke, for the owner: the problem's title and how it was chosen."""
    fault = session.fault or {}
    title = fault.get("title", "a problem")
    if session.fault_name == "custom":
        return f"{title} (their own idea: \"{fault.get('idea', '')}\")"
    if session.fault_name == "random":
        return f"{title} (picked by \"Pick one for me\")"
    return f"{title} (ready-made)"


def owner_note(session: Session, incident: Incident) -> None:
    """A short note to the owner whenever any visitor finishes an incident, so he sees the demo in use.

    Not for the owner's own shop: there he already gets the full messages. No visitor details are
    sent (no address, no name): only what was broken and how it ended.
    """
    if OWNER_CHAT_ID is None or session.telegram_chat_id == OWNER_CHAT_ID:
        return
    usage = incident.result["usage"] if incident.result else None
    agent = f"\nAgent: {usage.seconds:.0f} s, {usage.calls} AI calls" if usage else ""
    if incident.verification:
        verdict = incident.verification["verdict"]
        icon = {"FIXED": "✅", "FIXED FOR NOW": "🟡", "NOT FIXED": "❌"}.get(verdict, "")
        where = "on Telegram" if incident.decision["by"] == "Telegram" else "on the website"
        result = f"{icon} {verdict}: {incident.decision['note']} ({where})"
    else:
        result = f"❌ The agent stopped: {incident.error or 'no usable answer'}"
    in_background(send, OWNER_CHAT_ID,
                  f"👀 <b>Someone used the demo</b>\nProblem: {e(problem_name(session))}\nResult: {e(result)}{agent}")


incidents.subscribe("options", on_options)
incidents.subscribe("failed", on_failed)
incidents.subscribe("decided", on_decided)
incidents.subscribe("decided", owner_note)
incidents.subscribe("failed", owner_note)


# ---------- Messages and button taps from Telegram ----------

def parse_callback(data: str) -> tuple[str | None, str | None, int | None]:
    """'pick:1a2b3c4d:2' -> ('pick', '1a2b3c4d', 2). Anything else -> (None, None, None)."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] not in ("pick", "yes", "back") or not parts[2].isdigit():
        return None, None, None
    return parts[0], parts[1], int(parts[2])


def handle_update(update: dict) -> None:
    """One piece of news from Telegram: a message, or a tap on one of our buttons."""
    if "message" in update:
        handle_message(update["message"])
    elif "callback_query" in update:
        handle_button(update["callback_query"])


def handle_message(message: dict) -> None:
    """/start <code> links the chat; /stop unlinks it; anything else gets a short explanation."""
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    how_to_link = f"Open {e(page_url())} and tap \"Get alerts on Telegram\"."

    if text.startswith("/start"):
        code = text[len("/start"):].strip()
        session = find(lambda s: s.telegram_code == code) if code else None
        if session and session.telegram_code_until and datetime.now() <= session.telegram_code_until:
            link(session, chat_id)
            send(chat_id, "✅ <b>Linked!</b> This chat now gets the alerts for your Lifeguard-Agent demo shop.\n\n"
                          "Break something on the page: the agent starts by itself, messages you here with "
                          "the cause and the fixes, and you approve one with a tap.")
        else:
            send(chat_id, f"This link doesn't work any more (it's used once and expires after 15 minutes). {how_to_link}")
        return

    if text.startswith("/stop"):
        for session in find_all(lambda s: s.telegram_chat_id == chat_id):
            session.telegram_chat_id = None
        send(chat_id, "Alerts stopped for this chat. To start again: " + how_to_link)
        return

    if text.startswith("/myid"):
        # The owner needs this number for OWNER_CHAT_ID. Harmless for anyone else: it's their own chat.
        send(chat_id, f"This chat's id: <code>{chat_id}</code>")
        return

    send(chat_id, "I'm the Lifeguard-Agent pager: I message you when your demo shop breaks and let you "
                  f"approve the fix here. To link this chat: {how_to_link}")


def handle_button(query: dict) -> None:
    """A tap on Approve / Reject all (pick), Yes (yes) or Cancel (back).

    Only the chat linked to the session may decide, only on the incident the buttons were made for,
    and only once. "Yes" goes through incidents.decide_incident(), exactly like the website.
    """
    query_id = query["id"]
    chat_id = query["message"]["chat"]["id"]
    message_id = query["message"]["message_id"]
    action, incident_id, number = parse_callback(query.get("data", ""))
    session = find(lambda s: s.telegram_chat_id == chat_id)
    if action is None or session is None:
        answer(query_id, "This chat isn't linked to a demo shop any more.")
        return
    incident = session.incident
    if incident is None or incident.id != incident_id:
        answer(query_id, "These buttons belong to an older incident.")
        edit(chat_id, message_id, "These buttons belong to an older incident.")
        return
    if incident.decision is not None:
        answer(query_id, "Already decided: " + incident.decision["note"])
        return

    if action == "pick":
        answer(query_id)
        text, buttons = confirm_message(incident, number)
        edit(chat_id, message_id, text, buttons)
    elif action == "back":
        answer(query_id, "Cancelled, nothing changed.")
        text, buttons = options_message(incident)
        edit(chat_id, message_id, text, buttons)
    else:  # "yes": the human's approval
        final = incidents.final_call_view(incident.result["final"])
        title = final["options"][number - 1]["title"] if number else ""
        # The cause stays at the top of this message to the end, so the chat still tells the whole story
        # (problem, cause, fix, result) when someone scrolls back later (25 Oct, after Pavan's test).
        cause = f"🔎 <b>Cause:</b> {e(final['root_cause'])}\n" if final["root_cause"] else ""
        doing = f"Applying option {number}" if number else "Rejecting all options"
        answer(query_id, "Applying..." if number else "Rejecting...")
        edit(chat_id, message_id, f"{cause}⏳ {doing}...")
        try:
            incidents.decide_incident(session, number, approved_by="Telegram")
        except incidents.IncidentError as error:
            send(chat_id, e(error.message))
            return
        # The message now says how it ended, instead of "Applying..." forever (25 Oct, Pavan's test).
        decision = incident.decision
        if decision["approved"]:
            edit(chat_id, message_id, f"{cause}✔️ Applied option {number}: {e(title)}")
        elif number == 0:
            edit(chat_id, message_id, f"{cause}✔️ Rejected all options. Nothing was changed.")
        else:  # the safety check, run again at the moment of approval, stopped it
            edit(chat_id, message_id, f"{cause}✖️ Not applied: {e(decision['note'])}")
        # The verdict and the postmortem come from on_decided, the same as after a website decision.


# ---------- Receiving: webhook (live) or long polling (laptop) ----------

def poll_forever() -> None:
    """Laptop mode: ask Telegram for news again and again (each ask waits up to 30 s for something)."""
    offset = None
    while True:
        params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset  # "only news after this": each update is handled once
        updates = call("getUpdates", **params)
        if updates is None:
            time.sleep(5)  # no internet or a Telegram problem: wait, then try again
            continue
        for update in updates:
            offset = update["update_id"] + 1
            try:
                handle_update(update)
            except Exception:
                log.exception("A Telegram update failed")


def start() -> None:
    """Called once when the server starts: switch on the webhook (live) or polling (laptop)."""
    if not enabled():
        log.info("Telegram alerts are off (no TELEGRAM_BOT_TOKEN).")
        return

    def connect():
        name = bot_username()
        # What people see before pressing Start, and the menu of commands. Set by the code (not by hand
        # in BotFather), so the live bot and the Dev bot always say the same. Only the picture is manual.
        call("setMyCommands", commands=[
            {"command": "start", "description": "Link this chat (use the button on the demo page)"},
            {"command": "stop", "description": "Stop the alerts for this chat"},
            {"command": "myid", "description": "Show this chat's id"}])
        call("setMyShortDescription",
             short_description="The pager for Lifeguard-Agent: alerts and one-tap fix approval for your demo shop.")
        call("setMyDescription", description=(
            "I'm the pager for Lifeguard-Agent, an AI on-call engineer.\n\n"
            "On the demo page, tap \"Get alerts on Telegram\" or scan its QR code. When your demo shop breaks, "
            "the agent starts by itself and I message you the cause and the fixes. You approve one with a tap.\n\n"
            f"{page_url()}"))
        if PUBLIC_URL:
            done = call("setWebhook", url=f"{PUBLIC_URL}/telegram/webhook", secret_token=webhook_secret(),
                        allowed_updates=["message", "callback_query"], drop_pending_updates=True)
            log.info("Telegram @%s: webhook %s", name, "set" if done else "NOT set")
        else:
            # A bot with a webhook can't be polled, so remove any old one first.
            call("deleteWebhook")
            log.info("Telegram @%s: polling for messages (laptop mode)", name)
            poll_forever()

    in_background(connect)

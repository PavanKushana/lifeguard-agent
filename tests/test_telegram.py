"""
test_telegram.py
Checks the Telegram pager without talking to Telegram: every Bot API call is replaced by a recorder,
so each test sees exactly which messages would be sent, with which buttons.

What matters most: linking only works with a fresh one-time code, only the linked chat can decide,
approving takes two taps, and "Yes" goes through the same decide code as the website.
Run all tests with:  python -m pytest
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import telegram_bot
from app.incidents import decide_incident
from app.main import app
from app.sessions import get_or_create


@pytest.fixture
def sent(monkeypatch):
    """Record Bot API calls instead of sending them, and run 'background' work at once."""
    calls = []

    def fake_call(method, **params):
        calls.append((method, params))
        return {"message_id": len(calls), "username": "test_bot"}

    monkeypatch.setattr(telegram_bot, "call", fake_call)
    monkeypatch.setattr(telegram_bot, "send_document",
                        lambda chat_id, filename, content, caption: calls.append(("sendDocument", {"filename": filename})))
    monkeypatch.setattr(telegram_bot, "in_background", lambda function, *args: function(*args))
    return calls


def texts(calls):
    """Every message text that would have been sent or edited."""
    return [params.get("text", "") for method, params in calls if method in ("sendMessage", "editMessageText")]


def tap(chat_id, data):
    """A button tap from Telegram, as the webhook or polling would deliver it."""
    return {"callback_query": {"id": "q1", "data": data, "message": {"chat": {"id": chat_id}, "message_id": 7}}}


def test_parse_callback():
    assert telegram_bot.parse_callback("pick:1a2b3c4d:2") == ("pick", "1a2b3c4d", 2)
    assert telegram_bot.parse_callback("yes:1a2b3c4d:0") == ("yes", "1a2b3c4d", 0)
    assert telegram_bot.parse_callback("rm -rf:/:x") == (None, None, None)
    assert telegram_bot.parse_callback("pick:abc") == (None, None, None)


def test_start_with_a_fresh_code_links_the_chat(sent):
    session = get_or_create(None)
    code = telegram_bot.new_link_code(session)
    telegram_bot.handle_update({"message": {"chat": {"id": 111}, "text": f"/start {code}"}})
    assert session.telegram_chat_id == 111
    assert session.telegram_code is None  # used once
    assert "Linked" in texts(sent)[-1]


def test_a_code_works_once_and_expires(sent):
    session = get_or_create(None)
    code = telegram_bot.new_link_code(session)
    telegram_bot.handle_update({"message": {"chat": {"id": 111}, "text": f"/start {code}"}})
    telegram_bot.handle_update({"message": {"chat": {"id": 222}, "text": f"/start {code}"}})  # someone else, same link
    assert session.telegram_chat_id == 111
    old = get_or_create(None)
    old_code = telegram_bot.new_link_code(old)
    old.telegram_code_until = datetime.now() - timedelta(minutes=1)
    telegram_bot.handle_update({"message": {"chat": {"id": 333}, "text": f"/start {old_code}"}})
    assert old.telegram_chat_id is None
    assert "doesn't work any more" in texts(sent)[-1]


def test_start_without_a_code_says_what_the_bot_is(sent):
    # Start pressed on the bot itself (found by search), not from the page: no "link doesn't work" message.
    telegram_bot.handle_update({"message": {"chat": {"id": 121}, "text": "/start"}})
    assert "I'm the Lifeguard-Agent pager" in texts(sent)[-1]
    assert "doesn't work any more" not in texts(sent)[-1]


def test_one_chat_follows_one_shop(sent):
    first, second = get_or_create(None), get_or_create(None)
    telegram_bot.link(first, 444)
    telegram_bot.link(second, 444)
    assert first.telegram_chat_id is None and second.telegram_chat_id == 444


def test_alert_and_options_messages(sent, ready_incident):
    session = ready_incident("disk_full")
    session.telegram_chat_id = 555
    telegram_bot.send_alert(session)
    telegram_bot.on_options(session, session.incident)
    alert, options = texts(sent)
    assert "Customers report" in alert and "investigating" in alert
    assert "The backup job filled the database disk." in options
    buttons = sent[-1][1]["reply_markup"]["inline_keyboard"]
    assert buttons[0][0] == {"text": "Approve 1", "callback_data": f"pick:{session.incident.id}:1"}
    assert buttons[-1][0]["text"] == "Reject all"


def test_only_the_linked_chat_can_decide(sent, ready_incident):
    session = ready_incident("disk_full")
    session.telegram_chat_id = 666
    telegram_bot.handle_update(tap(999, f"yes:{session.incident.id}:1"))  # a stranger's chat
    assert session.incident.decision is None
    telegram_bot.handle_update(tap(666, "yes:00000000:1"))  # buttons from an older incident
    assert session.incident.decision is None


def test_approving_takes_two_taps_and_uses_the_shared_path(sent, ready_incident):
    session = ready_incident("disk_full")
    session.telegram_chat_id = 777
    incident_id = session.incident.id
    telegram_bot.handle_update(tap(777, f"pick:{incident_id}:1"))
    assert session.incident.decision is None  # picking alone is not approving
    assert "Apply option 1" in texts(sent)[-1]
    telegram_bot.handle_update(tap(777, f"yes:{incident_id}:1"))
    assert session.incident.decision["approved"] is True
    assert session.incident.decision["by"] == "Telegram"
    assert any(text.startswith("✅ <b>FIXED</b>") for text in texts(sent))
    assert ("sendDocument", {"filename": f"{session.incident.started:%Y-%m-%d_%H%M}_disk_full.md"}) in sent
    # The tapped message ends as "Applied", not "Applying..." forever, and keeps the cause at the top.
    assert ("🔎 <b>Cause:</b> The backup job filled the database disk.\n"
            "✔️ Applied option 1: Free space and stop the job") in texts(sent)


def test_rejecting_all_keeps_the_cause_too(sent, ready_incident):
    session = ready_incident("disk_full")
    session.telegram_chat_id = 888
    telegram_bot.handle_update(tap(888, f"yes:{session.incident.id}:0"))
    assert session.incident.decision["approved"] is False
    assert ("🔎 <b>Cause:</b> The backup job filled the database disk.\n"
            "✔️ Rejected all options. Nothing was changed.") in texts(sent)
    assert any(text.startswith("❌ <b>NOT FIXED</b>") for text in texts(sent))  # nothing changed: still broken


def test_owner_gets_a_note_when_someone_uses_the_demo(sent, ready_incident, monkeypatch):
    monkeypatch.setattr(telegram_bot, "OWNER_CHAT_ID", 4242)
    visitor = ready_incident("disk_full")  # a visitor on the website, without Telegram
    decide_incident(visitor, 1, approved_by="web visitor")
    notes = [params["text"] for method, params in sent if method == "sendMessage" and params["chat_id"] == 4242]
    assert len(notes) == 1
    assert "Someone used the demo" in notes[0] and "Database disk full (ready-made)" in notes[0]
    assert "FIXED" in notes[0] and "on the website" in notes[0]


def test_owner_gets_no_extra_note_for_his_own_shop(sent, ready_incident, monkeypatch):
    monkeypatch.setattr(telegram_bot, "OWNER_CHAT_ID", 4343)
    own = ready_incident("disk_full")
    own.telegram_chat_id = 4343  # the owner's shop: he already gets the full messages
    decide_incident(own, 1, approved_by="web visitor")
    assert not any("Someone used the demo" in text for text in texts(sent))


def test_myid_shows_the_chat_id(sent):
    telegram_bot.handle_update({"message": {"chat": {"id": 8080}, "text": "/myid"}})
    assert "8080" in texts(sent)[-1]


def test_link_endpoint_gives_a_qr_code(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TOKEN", "123:test-token")
    monkeypatch.setattr(telegram_bot, "_username", "lifeguard_agent_bot")
    link = TestClient(app).post("/telegram/link").json()
    assert link["url"] == f"https://t.me/lifeguard_agent_bot?start={link['code']}"
    assert link["qr_svg"].startswith("<svg") and link["bot"] == "lifeguard_agent_bot"


def test_qr_code_scales_instead_of_being_cut_off():
    # 25 Oct: the picture had a fixed size, the page showed it smaller, and the browser cut off two of
    # the three corner squares, so no phone could read it. Without a fixed size it scales to fit.
    svg = telegram_bot.qr_svg("https://t.me/lifeguard_agent_bot?start=abc")
    first_tag = svg.split(">", 1)[0]
    assert "viewBox" in first_tag and "width" not in first_tag and "height" not in first_tag


def test_webhook_refuses_calls_without_the_secret(monkeypatch):
    monkeypatch.setattr(telegram_bot, "TOKEN", "123:test-token")
    received = []
    monkeypatch.setattr(telegram_bot, "handle_update", received.append)
    client = TestClient(app)
    assert client.post("/telegram/webhook", json={"update_id": 1}).status_code == 403
    wrong = client.post("/telegram/webhook", json={"update_id": 2},
                        headers={"X-Telegram-Bot-Api-Secret-Token": "guess"})
    assert wrong.status_code == 403
    right = client.post("/telegram/webhook", json={"update_id": 3},
                        headers={"X-Telegram-Bot-Api-Secret-Token": telegram_bot.webhook_secret()})
    assert right.status_code == 200

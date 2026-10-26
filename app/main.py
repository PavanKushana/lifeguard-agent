"""
main.py
The web API: everything the terminal script does, available to a browser.

Run it with:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000 for the web page (files in static/),
or http://127.0.0.1:8000/docs to try every endpoint by hand.

This file only receives requests and sends answers. The real work stays in the
other files (simulator, agent, incidents, fixes), so the terminal, the web page and
the Telegram bot run exactly the same code.
"""

import asyncio
import json
import logging
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import telegram_bot
from app.fault_maker import make_fault, random_idea
from app.faults import PRESETS, apply_fault
from app.guard import check_fault_text
from app.incidents import (IncidentError, check_can_investigate, decide_incident, final_call_view, outcome,
                           postmortem as write_postmortem, proof_lines, start_investigation)
from app.limits import try_start_ai_run
from app.llm import Usage, describe_error
from app.sessions import COOKIE_NAME, IDLE_LIMIT, FaultBuild, Incident, Session, get_or_create
from app.simulator import Server

# The preset ids as a fixed list of choices, so /docs shows a dropdown instead of a text box
# and a typo is refused before our code runs. Built from PRESETS, so a new preset added in
# faults.py shows up here by itself, nothing to change in this file.
PresetId = Enum("PresetId", {preset_id: preset_id for preset_id in PRESETS}, type=str)

# The live stream looks for new steps this often (seconds), and gives up after this long
# (the agent takes under a minute; a stream nobody closes must not stay open forever).
STREAM_EVERY = 0.5
STREAM_LIMIT = timedelta(minutes=5)
# Breaking and resetting take turns: a fault finishing in the background at the same moment
# as a reset must not land half on the old server and half on the new one.
break_lock = threading.Lock()
# uvicorn's own logger: what's written here shows up in the terminal uvicorn runs in.
log = logging.getLogger("uvicorn.error")


class CustomIdea(BaseModel):
    """The visitor's fault idea, in their own words."""

    idea: str = Field(examples=["the cache runs out of memory and gets killed"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs when the server starts: connect the Telegram bot (webhook live, polling on a laptop)."""
    telegram_bot.start()
    yield


app = FastAPI(
    title="Lifeguard-Agent",
    description="AI SRE for small teams on one Docker host.",
    lifespan=lifespan,
)

# Compress answers over 1 KB for browsers that accept it (all do): the 3D picture's three.js is
# ~740 KB and goes over the wire as ~190 KB, which matters on a phone. The agent's live timeline
# (text/event-stream) is never compressed: Starlette skips that type, so each step still arrives at once.
app.add_middleware(GZipMiddleware, minimum_size=1000)

# The web page's files (HTML, CSS, JavaScript). Found from this file's own place, not from the
# folder uvicorn was started in, so the page works wherever the server is started from.
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# GET and HEAD: HEAD is GET without the body, the light "are you there?" check monitoring robots use.
# FastAPI's @app.get answers HEAD with 405, and UptimeRobot counted that as DOWN (25 Oct).
@app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
def home() -> FileResponse:
    """The web page. include_in_schema=False: it's a page for people, not an API endpoint, so /docs leaves it out."""
    return FileResponse(STATIC_DIR / "index.html")


def current_session(request: Request, response: Response) -> Session:
    """Find the visitor's session from their cookie (or start a new one) and keep the cookie fresh.

    Every endpoint that needs the visitor's server asks for this with Depends(current_session),
    so the cookie logic lives in one place.
    """
    session = get_or_create(request.cookies.get(COOKIE_NAME))
    # Remembered for an investigation that starts by itself later (Telegram linked): it counts too.
    session.last_ip = visitor_ip(request)
    # httponly: page scripts can't read the id. samesite=lax: other sites can't send requests with it.
    response.set_cookie(COOKIE_NAME, session.id, max_age=int(IDLE_LIMIT.total_seconds()),
                        httponly=True, samesite="lax")
    return session


def visitor_ip(request: Request) -> str:
    """The visitor's IP address, for the per-visitor spending limit.

    On Render every request passes Cloudflare first, which writes the real address into
    CF-Connecting-IP; a visitor can't fake that header, because Cloudflare overwrites it.
    Behind Caddy (the Nebius VM) the proxy writes it into X-Forwarded-For instead. Running
    locally there's no proxy, so it's the address of the connection itself.
    """
    if request.headers.get("cf-connecting-ip"):
        return request.headers["cf-connecting-ip"].strip()
    if request.headers.get("x-forwarded-for"):
        # The first entry is the visitor; later ones are proxies on the way.
        return request.headers["x-forwarded-for"].split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_spending(request: Request) -> None:
    """Count one AI run, or refuse it with 429 Too Many Requests and a sentence for the visitor.

    Called only after every other check passed, so a refused or impossible request costs no place.
    """
    reason = try_start_ai_run(visitor_ip(request))
    if reason:
        raise HTTPException(429, reason)


def iso(moment: datetime) -> str:
    """A time for the browser WITH its time zone, e.g. "2026-10-24T19:12:07+00:00".

    The live server runs on UTC. Without the "+00:00" a phone in Germany read "19:12" as German
    time, two hours ago, and the agent's clock showed "Working... 7221 s" (24 Oct, Pavan's phone test).
    """
    return moment.astimezone().isoformat(timespec="seconds")


def server_status(server: Server) -> dict:
    """What the dashboard shows: containers, disks, jobs and health checks, as plain JSON.

    Only what a real admin could see on the server. The fault and its hidden truth are never in here.
    """
    return {
        "containers": [
            {"name": c.name, "image": c.image, "status": c.status, "exit_code": c.exit_code,
             "restarts": c.restarts, "since": iso(c.since),
             "cpu_percent": c.cpu_percent, "memory_mb": c.memory_mb, "memory_limit_mb": c.memory_limit_mb}
            for c in server.containers.values()
        ],
        "disks": [
            {"mount": d.mount, "size_gb": d.size_gb, "used_gb": round(d.used_gb, 1),
             "used_percent": round(100 * d.used_gb / d.size_gb)}
            for d in server.disks.values()
        ],
        "jobs": [
            {"name": j.name, "schedule": j.schedule, "enabled": j.enabled}
            for j in server.jobs.values()
        ],
        "endpoints": [
            {"url": url, "status": result["status"], "ms": result.get("ms")}
            for url, result in server.endpoints.items()
        ],
        "memory_total_mb": server.memory_total_mb,
    }


@app.api_route("/health", methods=["GET", "HEAD"])
def health() -> dict:
    """Say the API is up. Checked by Render before each deploy goes live and by the uptime monitor
    every 5 minutes (which may ask with HEAD, see home())."""
    return {"status": "ok"}


@app.get("/status")
def status(session: Session = Depends(current_session)) -> dict:
    """Show the visitor's own server: healthy at first, broken after a fault, repaired after a fix."""
    # Only the alert, like a real on-call person gets: what users see, not why it happens.
    alert = session.fault["alert"] if session.fault else None
    incident = session.incident
    return {"session_started": iso(session.created), "alert": alert,
            # So the page notices what it didn't start itself: the agent starting on its own
            # (Telegram linked) or a fix decided on the phone.
            "incident": {"id": incident.id, "state": incident.state, "decided": incident.verification is not None}
                        if incident else None,
            "telegram": {"available": telegram_bot.enabled(), "linked": session.telegram_chat_id is not None},
            **server_status(session.server)}


@app.get("/presets")
def presets() -> list[dict]:
    """The ready-made faults the page offers as buttons. The visitor picks one, so its title can be shown."""
    return [{"id": fault["id"], "title": fault["title"], "alert": fault["alert"]} for fault in PRESETS.values()]


@app.post("/break/preset/{preset_id}")
def break_preset(preset_id: PresetId, session: Session = Depends(current_session)) -> dict:
    """Break the visitor's server with one of the preset faults, e.g. disk_full.

    An unknown id never reaches this function: FastAPI answers 422 and lists the allowed ids.
    """
    preset_id = preset_id.value
    with break_lock:
        check_can_break(session)
        session.fault = PRESETS[preset_id]
        session.fault_name = preset_id
        session.build = None  # an old failed build has nothing to say about this fault
        apply_fault(session.server, session.fault)
    after_break(session)
    return {"alert": session.fault["alert"], **server_status(session.server)}


def after_break(session: Session) -> None:
    """The on-call flow: if the visitor linked Telegram, the agent starts by itself and the phone buzzes.

    Without Telegram nothing happens here: the visitor starts the agent on the page.
    The investigation still counts against the visitor's spending limit; if that's reached, the
    phone gets the alert with the reason instead.
    """
    if session.telegram_chat_id is None:
        return
    refused = try_start_ai_run(session.last_ip)
    telegram_bot.send_alert(session, refused)
    if refused is None:
        try:
            start_investigation(session)
        except IncidentError:
            pass  # started a moment ago from the page: one investigation is enough


def check_can_break(session: Session) -> None:
    """Refuse a second fault while one is on the server or still being built (409)."""
    # One incident at a time: a second fault on top would mix two problems and two hidden truths.
    if session.fault is not None:
        raise HTTPException(409, "This server is already broken. Call POST /reset first.")
    if session.build is not None and session.build.state == "building":
        raise HTTPException(409, "A fault is still being built. Follow it with GET /break/status, or POST /reset.")


def build_view(session: Session) -> dict:
    """The fault build as JSON for the page. Never the fault itself: only the alert, once it's ready."""
    build = session.build
    return {"kind": build.kind, "idea": build.idea, "state": build.state, "message": build.message,
            "note": build.note, "started": iso(build.started), "cost": build.cost,
            "alert": session.fault["alert"] if build.state == "ready" else None}


def log_rejected(kind: str, idea: str, tries: list[dict]) -> None:
    """Write why Ultra's fault was rejected to the terminal. The visitor only gets one sentence."""
    lines = [f"  try {number}: {problem}" for number, attempt in enumerate(tries, start=1)
             for problem in attempt["problems"]]
    log.warning("A %s fault failed our checks (idea: %s)\n%s", kind, idea, "\n".join(lines))


def build_fault(session: Session, build: FaultBuild, idea: str, usage: Usage) -> None:
    """Let Ultra build the fault, then break the visitor's server with it. Runs in the background.

    Same steps as the terminal's --custom and --random: Ultra writes the fault, validator.py
    checks it, and Ultra gets one more try if the checks find problems.
    A random fault that still fails is started over once with a different idea: the visitor
    didn't choose it, so another one is just as good. (In a test on 24 Oct, 1 build in 22 failed
    the checks twice, and the visitor saw an error for a button that promised a surprise.)
    """
    try:
        fault, tries = make_fault(idea, usage)
        if fault is None:
            log_rejected(build.kind, idea, tries)
        # Only if the visitor is still waiting for this build: after a reset, it would waste tokens.
        if fault is None and build.kind == "random" and session.build is build:
            build.note = "The first problem didn't pass our checks, so a different one is being set up."
            idea = random_idea(other_than=idea)
            fault, tries = make_fault(idea, usage)
            if fault is None:
                log_rejected(build.kind, idea, tries)
        if build.kind == "random":
            failure = "Couldn't set up a problem this time (it didn't pass our checks). Please press Pick one for me again."
        else:
            failure = "Couldn't build this fault: it didn't pass our checks. Please try again or describe it differently."
    except Exception as error:  # e.g. Token Factory busy or down
        # The visitor gets one clear sentence; the terminal gets the full error to look into.
        log.exception("Building a %s fault failed", build.kind)
        fault, failure = None, f"Couldn't build this fault. {describe_error(error)}"
    build.cost = usage.summary()

    with break_lock:
        # The visitor may have pressed reset while Ultra worked: then this fault is thrown away.
        if session.build is not build:
            return
        if fault is None:
            build.state, build.message = "failed", failure
            return
        session.fault, session.fault_name = fault, build.kind
        apply_fault(session.server, fault)
        build.state = "ready"
        build.message = "Your server is broken now. Read the alert, then POST /investigate."
    after_break(session)  # outside the lock: it may start the agent, which takes a while to set up


def start_build(session: Session, kind: str, idea: str, usage: Usage) -> dict:
    """Start building a custom or random fault in the background and answer at once."""
    with break_lock:
        # Checked again: the guard took a few seconds, and a second click may have got in first.
        check_can_break(session)
        # The random idea stays secret (idea=None): it would tell the visitor where to look.
        session.build = FaultBuild(kind=kind, idea=idea if kind == "custom" else None)
    # daemon=True: if the API stops, it doesn't wait for a half-built fault.
    threading.Thread(target=build_fault, args=(session, session.build, idea, usage), daemon=True).start()
    return build_view(session)


@app.post("/break/custom")
def break_custom(body: CustomIdea, request: Request, session: Session = Depends(current_session)) -> dict:
    """Break the server with a fault the visitor describes, e.g. "the cache runs out of memory".

    First the guard checks the text (free rules, then Nano), so only faults for our server get built;
    a refusal comes back at once as 400 with the reason. Then Ultra builds the fault in the
    background (10 seconds to 2 minutes): follow it with GET /break/status.
    Counts as one AI run for the spending limit (429 when it's reached).
    """
    check_can_break(session)  # before the guard, so a refused break costs no tokens
    check_spending(request)
    usage = Usage()
    verdict = check_fault_text(body.idea, usage)
    if not verdict["allowed"]:
        raise HTTPException(400, verdict["reason"])
    return start_build(session, "custom", body.idea.strip(), usage)


@app.post("/break/random")
def break_random(request: Request, session: Session = Depends(current_session)) -> dict:
    """ "Surprise me": Ultra builds a fault from one of our own ideas, kept secret until the end.

    No guard needed, the idea is ours. Follow the build with GET /break/status.
    Counts as one AI run for the spending limit (429 when it's reached).
    """
    check_can_break(session)  # first, so a click on an already broken server costs no place
    check_spending(request)
    return start_build(session, "random", random_idea(), Usage())


@app.get("/break/status")
def break_status(session: Session = Depends(current_session)) -> dict:
    """How the custom or random fault is coming along: "building", then "ready" (with the alert) or "failed"."""
    if session.build is None:
        raise HTTPException(404, "No custom or random fault started. (Presets break at once, no build needed.)")
    return build_view(session)


@app.post("/reset")
def reset(session: Session = Depends(current_session)) -> dict:
    """Give the visitor a fresh healthy server, whatever state the old one was in."""
    with break_lock:
        session.reset()
    return {"alert": None, **server_status(session.server)}


def incident_view(incident: Incident) -> dict:
    """The incident as JSON for the page: state, timeline so far, and at the end the cost and fix options."""
    view = {"id": incident.id, "state": incident.state, "alert": incident.alert,
            "started": iso(incident.started),
            "steps": incident.steps, "error": incident.error}
    if incident.result:
        view["cost"] = incident.result["usage"].summary()
        # The final call can be None when Ultra's reply couldn't be read; then there is nothing to choose.
        if incident.result.get("final"):
            view["final_call"] = final_call_view(incident.result["final"])
    # Both: in the split second between the decision and the end of the check there's no verdict yet.
    if incident.decision and incident.verification:
        view["decision"] = incident.decision["note"]
        view["verdict"] = incident.verification["verdict"]
        view["proof"] = incident.proof
        # The same words Telegram uses, made once in incidents.py: what works again, what's still
        # wrong (FIXED FOR NOW), what to watch, and the proof lines in plain words.
        view["outcome"] = outcome(incident)
        view["proof_lines"] = proof_lines(incident.proof)
        view["checks"] = {key: incident.verification[key] for key in ("users_ok", "stays_fixed", "warnings")}
    return view


@app.post("/investigate")
def investigate(request: Request, session: Session = Depends(current_session)) -> dict:
    """Start the agent on the visitor's broken server. Answers at once; the agent keeps working in the background.

    The agent takes 20-40 seconds. A request that waited that long would look frozen, so this only
    starts it, and the page follows the steps with GET /incident.
    Counts as one AI run for the spending limit (429 when it's reached).
    """
    try:
        check_can_investigate(session)  # first, so an impossible request doesn't use a spending place
        check_spending(request)
        return incident_view(start_investigation(session))
    except IncidentError as error:
        raise HTTPException(error.status, error.message)


@app.get("/incident")
def incident(session: Session = Depends(current_session)) -> dict:
    """The agent's timeline so far. Call it again to see new steps, until state is "done" or "failed"."""
    if session.incident is None:
        raise HTTPException(404, "No investigation yet. Call POST /investigate first.")
    return incident_view(session.incident)


def sse(event: str, data: dict) -> str:
    """One Server-Sent Event: an event name and a line of JSON, ended by an empty line. That's the whole format."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/incident/stream")
async def incident_stream(session: Session = Depends(current_session)) -> StreamingResponse:
    """The agent's steps live, each one sent the moment it happens (Server-Sent Events).

    Instead of the page asking GET /incident again and again, it opens this once and the server
    pushes every new step as a "step" event. At the end comes one "done" (or "failed") event with
    the whole incident, fix options included, and the stream closes.
    Try it: open http://127.0.0.1:8000/incident/stream in a browser tab right after POST /investigate.
    """
    incident = session.incident
    if incident is None:
        raise HTTPException(404, "No investigation yet. Call POST /investigate first.")

    async def events():
        sent = 0  # how many steps this visitor already got
        deadline = datetime.now() + STREAM_LIMIT
        while datetime.now() < deadline:
            # A reset starts a new incident: this one is over for the visitor.
            if session.incident is not incident:
                yield sse("reset", {"message": "The server was reset. Start a new investigation."})
                return
            # Read the state BEFORE the steps: the agent adds its last step before it says "done",
            # so when this says done, every step is already in the list and none gets lost.
            state = incident.state
            new_steps = incident.steps[sent:]
            for step in new_steps:
                yield sse("step", step)
            sent += len(new_steps)
            if state != "running":
                yield sse(state, incident_view(incident))  # "done" or "failed"
                return
            # async sleep: while this visitor waits, the server keeps answering everyone else.
            await asyncio.sleep(STREAM_EVERY)
        yield sse("timeout", {"message": "Still running after 5 minutes. Check GET /incident."})

    # no-cache: a cached copy of a live stream would be stale the moment it was saved.
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.post("/decide/{option}")
def decide(option: int, session: Session = Depends(current_session)) -> dict:
    """The human's decision: approve fix option 1, 2 or 3, or 0 to reject them all.

    Read the options first: GET /incident shows them under "final_call" at the bottom.

    An approved fix is applied to the visitor's server and checked right away, like in the terminal:
    FIXED, FIXED FOR NOW or NOT FIXED. A rejection gets a verdict too (nothing changed, so NOT FIXED).
    """
    # The same code as a decision from Telegram (incidents.decide_incident): one approval path.
    try:
        result = decide_incident(session, option, approved_by="web visitor")
    except IncidentError as error:
        raise HTTPException(error.status, error.message)
    incident = session.incident
    # The answer first: after deciding, "did it work?" matters most, and it was hidden below ~30 steps.
    # The whole incident follows (its own "verdict"/"decision" keys keep these first places).
    return {"verdict": result["verdict"], "decision": incident.decision["note"],
            **incident_view(incident), "server": server_status(session.server)}


@app.get("/postmortem", response_class=PlainTextResponse)
def postmortem(session: Session = Depends(current_session)) -> PlainTextResponse:
    """The incident report in markdown: what happened, when, why, what fixed it, what's next.

    Only after POST /decide, because the decision and the verdict are part of the story.
    Built by the same code as the terminal's postmortem, from what was recorded, so it costs no tokens.
    """
    try:
        text, filename = write_postmortem(session)  # the same report Telegram sends as a file
    except IncidentError as error:
        raise HTTPException(error.status, error.message)
    # "inline" shows the text in /docs and in the browser; the filename is used if someone saves it.
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'inline; filename="{filename}"'})


# ---------- Telegram (Day 10) ----------

@app.post("/telegram/link")
def telegram_link(session: Session = Depends(current_session)) -> dict:
    """A one-time link that opens our bot; pressing Start in Telegram links that chat to this visitor's shop.

    Then the shop breaking starts the agent by itself, and the phone gets the alert and the fix buttons.
    """
    if not telegram_bot.enabled() or telegram_bot.bot_username() is None:
        raise HTTPException(404, "Telegram alerts aren't set up on this server.")
    code = telegram_bot.new_link_code(session)
    url = telegram_bot.link_url(code)
    # Three ways to use it: open the link (Telegram on this device), scan the QR code with a phone,
    # or send "/start <code>" to the bot by hand.
    return {"url": url, "qr_svg": telegram_bot.qr_svg(url), "code": code, "bot": telegram_bot.bot_username(),
            "linked": session.telegram_chat_id is not None}


@app.post("/telegram/unlink")
def telegram_unlink(session: Session = Depends(current_session)) -> dict:
    """Stop sending this visitor's alerts to Telegram."""
    session.telegram_chat_id = None
    return {"linked": False}


@app.post("/telegram/webhook", include_in_schema=False)
async def telegram_webhook(request: Request) -> dict:
    """Live mode: Telegram calls this with every message and button tap for our bot.

    Only Telegram knows the secret header (we gave it in setWebhook), so fake calls are refused.
    The work runs in the background: Telegram only needs a quick "got it".
    """
    secret = request.headers.get("x-telegram-bot-api-secret-token", "")
    # compare_digest: takes the same time for every wrong guess, so the secret can't be guessed bit by bit.
    if not telegram_bot.enabled() or not secrets.compare_digest(secret, telegram_bot.webhook_secret()):
        raise HTTPException(403, "Not from Telegram.")
    update = await request.json()
    threading.Thread(target=telegram_bot.handle_update, args=(update,), daemon=True).start()
    return {"ok": True}

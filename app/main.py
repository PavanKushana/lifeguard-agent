"""
main.py
The web API: everything the terminal script does, available to a browser.

Run it with:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs to try every endpoint by hand.

This file only receives requests and sends answers. The real work stays in the
other files (simulator, agent, approval, fixes), so the terminal and the web page
run exactly the same code.
"""

import threading
from enum import Enum

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from app.agent import format_step, handle_incident
from app.approval import approve_option
from app.faults import PRESETS, apply_fault
from app.fixes import apply_fix, verify
from app.postmortem import build_postmortem
from app.sessions import COOKIE_NAME, IDLE_LIMIT, Incident, Session, get_or_create
from app.simulator import Server

# The preset ids as a fixed list of choices, so /docs shows a dropdown instead of a text box
# and a typo is refused before our code runs. Built from PRESETS, so a new preset added in
# faults.py shows up here by itself, nothing to change in this file.
PresetId = Enum("PresetId", {preset_id: preset_id for preset_id in PRESETS}, type=str)

# Only one decision at a time: a double click on "Approve" must not apply the same fix twice.
decide_lock = threading.Lock()

app = FastAPI(
    title="Lifeguard Agent",
    description="AI SRE for small teams on one Docker host.",
)


def current_session(request: Request, response: Response) -> Session:
    """Find the visitor's session from their cookie (or start a new one) and keep the cookie fresh.

    Every endpoint that needs the visitor's server asks for this with Depends(current_session),
    so the cookie logic lives in one place.
    """
    session = get_or_create(request.cookies.get(COOKIE_NAME))
    # httponly: page scripts can't read the id. samesite=lax: other sites can't send requests with it.
    response.set_cookie(COOKIE_NAME, session.id, max_age=int(IDLE_LIMIT.total_seconds()),
                        httponly=True, samesite="lax")
    return session


def server_status(server: Server) -> dict:
    """What the dashboard shows: containers, disks, jobs and health checks, as plain JSON.

    Only what a real admin could see on the server. The fault and its hidden truth are never in here.
    """
    return {
        "containers": [
            {"name": c.name, "image": c.image, "status": c.status, "exit_code": c.exit_code,
             "restarts": c.restarts, "since": c.since.isoformat(timespec="seconds"),
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


@app.get("/health")
def health() -> dict:
    """Say the API is up. Used to check the server started (and later by the host)."""
    return {"status": "ok"}


@app.get("/status")
def status(session: Session = Depends(current_session)) -> dict:
    """Show the visitor's own server: healthy at first, broken after a fault, repaired after a fix."""
    # Only the alert, like a real on-call person gets: what users see, not why it happens.
    alert = session.fault["alert"] if session.fault else None
    return {"session_started": session.created.isoformat(timespec="seconds"), "alert": alert,
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
    # One incident at a time: a second fault on top would mix two problems and two hidden truths.
    if session.fault is not None:
        raise HTTPException(409, "This server is already broken. Call POST /reset first.")
    session.fault = PRESETS[preset_id]
    session.fault_name = preset_id
    apply_fault(session.server, session.fault)
    return {"alert": session.fault["alert"], **server_status(session.server)}


@app.post("/reset")
def reset(session: Session = Depends(current_session)) -> dict:
    """Give the visitor a fresh healthy server, whatever state the old one was in."""
    session.reset()
    return {"alert": None, **server_status(session.server)}


def run_agent(server: Server, incident: Incident) -> None:
    """Run the whole agent (triage, investigation, final call) and store the results. Runs in the background.

    Any error is caught and recorded, so the page can show "failed" instead of waiting forever.
    """
    try:
        incident.result = handle_incident(server, incident.alert, incident.add_step)
        incident.state = "done"
    except Exception as error:  # e.g. Token Factory down, or a missing API key
        incident.error = f"{type(error).__name__}: {error}"
        incident.state = "failed"


def final_call_view(final: dict) -> dict:
    """Ultra's fix options as separate fields, so a human can read them before POST /decide.

    The same facts as the "final_call" step, but that step is one long text for the timeline.
    Here every option has its own number, steps, pros, cons and risk: readable in /docs now,
    and the web page (Day 8) turns them into option cards with an Approve button.
    """
    options = [{"number": number,
                "title": option.get("title"),
                "recommended": bool(option.get("recommended")),
                "steps": [format_step(step) for step in option["steps"]],
                "pros": option["pros"],
                "cons": option["cons"],
                "undo": option["undo"],
                "can_be_undone": option["reversible"],
                "risk": option["risk"]}
               for number, option in enumerate(final["options"], start=1)]

    if any(option["recommended"] for option in options):
        why = final.get("why_recommended")
    else:
        # Same rule as the terminal: don't show praise for an option safety took away.
        why = ("Ultra's recommended option was blocked by the safety check, so none of these is "
               "recommended. Compare the cons before picking.")
    if options:
        how_to_decide = (f"POST /decide/{{option}} with a number from 1 to {len(options)} to approve "
                         "that option, or 0 to reject them all.")
    else:
        how_to_decide = "No safe option is left. POST /decide/0 to reject, and fix it by hand."

    return {"root_cause": final.get("root_cause"),
            "confidence": final.get("confidence"),
            "options": options,
            "why_recommended": why,
            "blocked": final.get("blocked", []),  # each one {"title", "reason"}: the human sees what safety stopped
            "how_to_decide": how_to_decide}


def incident_view(incident: Incident) -> dict:
    """The incident as JSON for the page: state, timeline so far, and at the end the cost and fix options."""
    view = {"state": incident.state, "alert": incident.alert,
            "started": incident.started.isoformat(timespec="seconds"),
            "steps": incident.steps, "error": incident.error}
    if incident.result:
        view["cost"] = incident.result["usage"].summary()
        # The final call can be None when Ultra's reply couldn't be read; then there is nothing to choose.
        if incident.result.get("final"):
            view["final_call"] = final_call_view(incident.result["final"])
    if incident.decision:
        view["decision"] = incident.decision["note"]
        view["verdict"] = incident.verification["verdict"]
    return view


@app.post("/investigate")
def investigate(session: Session = Depends(current_session)) -> dict:
    """Start the agent on the visitor's broken server. Answers at once; the agent keeps working in the background.

    The agent takes 20-40 seconds. A request that waited that long would look frozen, so this only
    starts it, and the page follows the steps with GET /incident.
    """
    if session.fault is None:
        raise HTTPException(409, "Nothing is broken yet. Break the server first.")
    if session.incident is not None:
        raise HTTPException(409, "The agent already ran on this fault. Call POST /reset to start again.")
    session.incident = Incident(alert=session.fault["alert"])
    # daemon=True: if the API stops, it doesn't wait for a half-finished agent.
    threading.Thread(target=run_agent, args=(session.server, session.incident), daemon=True).start()
    return incident_view(session.incident)


@app.get("/incident")
def incident(session: Session = Depends(current_session)) -> dict:
    """The agent's timeline so far. Call it again to see new steps, until state is "done" or "failed"."""
    if session.incident is None:
        raise HTTPException(404, "No investigation yet. Call POST /investigate first.")
    return incident_view(session.incident)


@app.post("/decide/{option}")
def decide(option: int, session: Session = Depends(current_session)) -> dict:
    """The human's decision: approve fix option 1, 2 or 3, or 0 to reject them all.

    Read the options first: GET /incident shows them under "final_call" at the bottom.

    An approved fix is applied to the visitor's server and checked right away, like in the terminal:
    FIXED, FIXED FOR NOW or NOT FIXED. A rejection gets a verdict too (nothing changed, so NOT FIXED).
    """
    incident = session.incident
    if incident is None or incident.state != "done":
        raise HTTPException(409, "There are no fix options yet. Wait until GET /incident says done.")
    final = incident.result["final"]
    options = final.get("options", []) if final else []
    if not 0 <= option <= len(options):
        raise HTTPException(400, f"There is no option {option}. Choose 1 to {len(options)}, or 0 to reject all.")

    with decide_lock:
        if incident.decision is not None:
            raise HTTPException(409, "A decision was already made for this incident. Call POST /reset to start again.")
        # The same function the terminal uses: it runs the safety check again before approving.
        incident.decision = approve_option(session.server, final, option, approved_by="web visitor")
        incident.done = apply_fix(session.server, incident.decision)

    if incident.done:
        incident.add_step("apply", "\n".join(f"{number}. {line}" for number, line in enumerate(incident.done, 1)))
    incident.verification = verify(session.server, session.fault)
    result = incident.verification
    lines = [result["verdict"], f"Decision: {incident.decision['note']}", "", "Users OK?"]
    lines += [f"  {line}" for line in result["users_ok"]] + ["Stays fixed?"]
    lines += [f"  {line}" for line in result["stays_fixed"]]
    if result["warnings"]:
        lines += ["Watch out:"] + [f"  {line}" for line in result["warnings"]]
    incident.add_step("verdict", "\n".join(lines))
    return {**incident_view(incident), "server": server_status(session.server)}


@app.get("/postmortem", response_class=PlainTextResponse)
def postmortem(session: Session = Depends(current_session)) -> PlainTextResponse:
    """The incident report in markdown: what happened, when, why, what fixed it, what's next.

    Only after POST /decide, because the decision and the verdict are part of the story.
    Built by the same code as the terminal's postmortem, from what was recorded, so it costs no tokens.
    """
    incident = session.incident
    if incident is None or incident.decision is None:
        raise HTTPException(409, "No decision yet. Run the incident and POST /decide first.")
    if incident.verification is None:  # the fix is applied but the check is still running (a split second)
        raise HTTPException(409, "The fix is still being checked. Try again in a moment.")

    text = build_postmortem({**incident.result, "alert": incident.alert, "started": incident.started,
                             "times": incident.times, "decision": incident.decision, "done": incident.done,
                             "verification": incident.verification})
    # "inline" shows the text in /docs and in the browser; the filename is used if someone saves it.
    filename = f"{incident.started:%Y-%m-%d_%H%M}_{session.fault_name}.md"
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'inline; filename="{filename}"'})

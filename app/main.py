"""
main.py
The web API: everything the terminal script does, available to a browser.

Run it with:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000/docs to try every endpoint by hand.

This file only receives requests and sends answers. The real work stays in the
other files (simulator, agent, approval, fixes), so the terminal and the web page
run exactly the same code.
"""

from enum import Enum

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from app.faults import PRESETS, apply_fault
from app.sessions import COOKIE_NAME, IDLE_LIMIT, Session, get_or_create
from app.simulator import Server

# The preset ids as a fixed list of choices, so /docs shows a dropdown instead of a text box
# and a typo is refused before our code runs. Built from PRESETS, so a new preset added in
# faults.py shows up here by itself, nothing to change in this file.
PresetId = Enum("PresetId", {preset_id: preset_id for preset_id in PRESETS}, type=str)

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

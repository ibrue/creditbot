"""Browser client for the credit system.

Runs inside the same FastAPI app as the kiosk API, so the NAS serves both
on port 8765: the kiosk keeps using X-API-Key, while people use a browser
with a shared lab password and a signed session cookie.

Config (environment):
  WEB_ENABLED     1 (default) to serve the web client, 0 to disable
  WEB_PASSWORD    the shared lab password (required — unset serves nothing)
  WEB_SECRET      optional; a signing key is generated and kept if unset
  WEB_HTTPS       1 when reached over HTTPS, so cookies are marked Secure
  WEB_TRUST_PROXY 1 only when a reverse proxy you control sets
                  X-Forwarded-For; otherwise the header is ignored

Two login pages are served:
  /app          multi-user — after the password, people sign in with face
                recognition (if the server has the models) or by picking
                their name.
  /app/station  single-identity — for the shared desktop. The password
                signs the machine straight in as the station account
                (WEB_STATION_ID / WEB_STATION_NAME), no picker.
"""
import os
from datetime import datetime

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import checkin_logic
import config
import database
import web_auth
import web_face
from utils.helpers import format_duration, get_credit_tier

WEB_ENABLED = os.getenv("WEB_ENABLED", "1") == "1"
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")
WEB_HTTPS = os.getenv("WEB_HTTPS", "0") == "1"
WEB_TRUST_PROXY = os.getenv("WEB_TRUST_PROXY", "0") == "1"

# The shared account that /app/station signs in as (the lab desktop).
WEB_STATION_ID = os.getenv("WEB_STATION_ID", "station")
WEB_STATION_NAME = os.getenv("WEB_STATION_NAME", "Lab Computer")

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
INDEX_FILE = os.path.join(WEB_DIR, "index.html")

_SECRET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(config.DATABASE_PATH)) or ".", ".web_secret")

router = APIRouter()
limiter = web_auth.LoginLimiter()

_secret_cache: bytes | None = None


def secret() -> bytes:
    global _secret_cache
    if _secret_cache is None:
        _secret_cache = web_auth.load_secret(_SECRET_PATH)
    return _secret_cache


def client_key(request: Request) -> str:
    """Rate-limit key for the caller.

    X-Forwarded-For is only honored when WEB_TRUST_PROXY says a proxy we
    control sets it. Trusting it unconditionally would hand every caller a
    free rate-limit reset — invent a new value per request and the login
    limiter never fires, which is the only thing standing between a shared
    password and an offline-speed guessing attack.
    """
    if WEB_TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def current_session(token: str | None) -> dict | None:
    return web_auth.read_token(token, secret()) if token else None


def require_session(token: str | None) -> dict:
    session = current_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return session


def require_person(session: dict) -> tuple[str, str]:
    discord_id = session.get("discord_id")
    name = session.get("name")
    if not discord_id or not name:
        raise HTTPException(status_code=409, detail="Pick who you are first")
    return str(discord_id), str(name)


def set_session_cookie(response: Response, payload: dict):
    response.set_cookie(
        web_auth.SESSION_COOKIE,
        web_auth.make_token(payload, secret()),
        max_age=int(web_auth.SESSION_HOURS * 3600),
        httponly=True,      # not readable from JavaScript
        samesite="lax",     # not sent on cross-site form posts
        secure=WEB_HTTPS,   # HTTPS-only when the deployment uses it
        path="/",
    )


# ------------------------------------------------------------- the page

@router.get("/", include_in_schema=False)
def index():
    if not WEB_ENABLED:
        raise HTTPException(status_code=404, detail="Web client is disabled")
    if not os.path.exists(INDEX_FILE):
        raise HTTPException(status_code=500, detail="Web client files are missing")
    return FileResponse(INDEX_FILE, media_type="text/html")


@router.get("/station", include_in_schema=False)
def station_page():
    """Same page as /; the script reads the URL and becomes station mode."""
    return index()


# ---------------------------------------------------------------- login

class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    # True on the /app/station page: the password signs the machine in as
    # the shared station account directly, skipping the identity step.
    station: bool = False


@router.post("/api/login")
def login(req: LoginRequest, request: Request):
    if not WEB_ENABLED:
        raise HTTPException(status_code=404, detail="Web client is disabled")
    if not WEB_PASSWORD:
        raise HTTPException(
            status_code=503,
            detail="WEB_PASSWORD is not set on the server — the web client "
                   "cannot be used until it is.",
        )

    key = client_key(request)
    allowed, retry_after = limiter.check(key)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Too many attempts. Try again in "
                               f"{retry_after // 60 + 1} minute(s)."},
            headers={"Retry-After": str(retry_after)},
        )

    if not web_auth.password_matches(req.password, WEB_PASSWORD):
        limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Wrong password")

    limiter.reset(key)
    payload = {}
    if req.station:
        database.get_or_create_user(WEB_STATION_ID, WEB_STATION_NAME)
        payload = {"discord_id": WEB_STATION_ID, "name": WEB_STATION_NAME,
                   "station": True}
    response = JSONResponse(content={"status": "ok", "station": req.station})
    set_session_cookie(response, payload)
    return response


@router.post("/api/logout")
def logout():
    response = JSONResponse(content={"status": "ok"})
    response.delete_cookie(web_auth.SESSION_COOKIE, path="/")
    return response


# ------------------------------------------------------------- identity

class SelectRequest(BaseModel):
    discord_id: str = Field(min_length=1, max_length=32)


@router.get("/api/me")
def me(creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id = session.get("discord_id")
    if not discord_id:
        return {"signed_in": True, "person": None}

    user = database.get_user(str(discord_id))
    if not user:
        return {"signed_in": True, "person": None}

    active = database.get_active_checkin(str(discord_id))
    tier, emoji = get_credit_tier(user["total_credits"])
    minutes = 0
    if active:
        started = datetime.fromisoformat(active["checkin_time"])
        minutes = int((datetime.now() - started).total_seconds() / 60)

    return {
        "signed_in": True,
        "person": {
            "discord_id": user["discord_id"],
            "name": user["username"],
            "total_credits": user["total_credits"],
            "weekly_credits": user["weekly_credits"],
            "streak": user["current_streak"],
            "tier": tier,
            "tier_emoji": emoji,
            "checked_in": active is not None,
            "checked_in_for": format_duration(minutes) if active else None,
        },
    }


@router.get("/api/members")
def members(creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    return {"members": database.get_all_users()}


@router.post("/api/select")
def select(req: SelectRequest, creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    user = database.get_user(req.discord_id)
    if not user:
        raise HTTPException(status_code=404, detail="No such member")

    response = JSONResponse(content={
        "status": "ok",
        "discord_id": user["discord_id"],
        "name": user["username"],
    })
    set_session_cookie(response, {"discord_id": user["discord_id"],
                                  "name": user["username"]})
    return response


# ----------------------------------------------------------- face login

class FaceLoginRequest(BaseModel):
    # One JPEG webcam frame, base64. ~8MB cap matches the kiosk photo cap.
    image_b64: str = Field(min_length=1, max_length=8_000_000)


@router.get("/api/face-status")
def face_status(creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    return {"available": web_face.available()}


@router.post("/api/face-login")
def face_login(req: FaceLoginRequest,
               creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    try:
        match = web_face.identify(req.image_b64)
    except web_face.FaceLoginError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    user = database.get_user(match["discord_id"])
    name = user["username"] if user else match["name"]
    print(f"🌐 Web face login: {name} (score {match['score']:.2f})")
    response = JSONResponse(content={"status": "ok",
                                     "discord_id": match["discord_id"],
                                     "name": name,
                                     "score": round(match["score"], 3)})
    set_session_cookie(response, {"discord_id": match["discord_id"],
                                  "name": name})
    return response


# ------------------------------------------------------------ check in/out

@router.post("/api/checkin")
def checkin(creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id, name = require_person(session)
    result = checkin_logic.perform_checkin(discord_id, name, source="web")
    print(f"🌐 Web check-in: {name} -> {result['status']}")
    return result


@router.post("/api/checkout")
def checkout(creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id, name = require_person(session)
    result = checkin_logic.perform_checkout(discord_id)
    print(f"🌐 Web check-out: {name} -> {result['status']}")
    return result


# ----------------------------------------------------------------- views

@router.get("/api/whos-in")
def whos_in(creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    people = []
    for row in database.get_all_checked_in():
        started = datetime.fromisoformat(row["checkin_time"])
        minutes = int((datetime.now() - started).total_seconds() / 60)
        people.append({
            "discord_id": row["discord_id"],
            "name": row["username"],
            "duration": format_duration(minutes),
        })
    return {"checked_in": people}


@router.get("/api/leaderboard")
def leaderboard(creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    return {
        "weekly": database.get_weekly_leaderboard(10),
        "alltime": database.get_all_time_leaderboard(10),
    }


@router.get("/api/history")
def history(creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id, _ = require_person(session)
    return {"transactions": database.get_transactions(discord_id, limit=15)}

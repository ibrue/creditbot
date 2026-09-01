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

The deployment is a walk-up terminal, not a personal app:
  /app          the terminal. The lab password arms it once, then each
  /app/kiosk    press of Check in / Check out identifies the person in
                front of the camera and credits them, returning to idle.
                Both paths serve the same screen.
  /app/enroll   registration — no password, and refused over Tailscale
                Funnel so it stays a lab-network door.
  /app/station  retired; redirects to /app/kiosk. It credited one shared
                account for everyone at the lab computer.

The per-person dashboard that used to live at /app was removed: on a
shared terminal it was the wrong shape, and its Sign out button de-armed
the machine for whoever came next.
"""
import base64
import json
import os
import secrets
from datetime import datetime

from fastapi import APIRouter, Cookie, HTTPException, Request, Response
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse,
                               Response)
from pydantic import BaseModel, Field

import checkin_logic
import config
import database
import discord_lookup
import web_auth
import web_face
from utils.helpers import format_duration, get_credit_tier

WEB_ENABLED = os.getenv("WEB_ENABLED", "1") == "1"
WEB_PASSWORD = os.getenv("WEB_PASSWORD", "")


def lab_password() -> str:
    """The shared lab password.

    Read at each use rather than captured at import, so it can be rotated
    from the admin page without a shell on the server. An override saved
    there wins over .env, the same way the Discord settings do. Rotating it
    does not sign anyone out: session cookies are signed with WEB_SECRET,
    so an armed terminal stays armed.
    """
    return config.setting("WEB_PASSWORD", "") or WEB_PASSWORD
WEB_HTTPS = os.getenv("WEB_HTTPS", "0") == "1"
WEB_TRUST_PROXY = os.getenv("WEB_TRUST_PROXY", "0") == "1"

# The shared account that /app/station signs in as (the lab desktop).
WEB_STATION_ID = os.getenv("WEB_STATION_ID", "station")
WEB_STATION_NAME = os.getenv("WEB_STATION_NAME", "Lab Computer")

# Self-enrolment at /app/enroll deliberately needs no password: a new
# member walks up to the lab terminal and enrols themselves. Requests
# arriving over Tailscale Funnel (i.e. from the public internet) are
# refused anyway, because otherwise anyone who found the URL could
# enrol their own face under someone else's name. Set
# WEB_ENROLL_PUBLIC=1 to allow enrolment from the internet too.
WEB_ENROLL_PUBLIC = os.getenv("WEB_ENROLL_PUBLIC", "0") == "1"

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
    """Retired — the walk-up kiosk replaced it.

    The station page signed the machine in as one shared account, so every
    check-in at the lab computer credited "Lab Computer" rather than the
    person standing there, and that account climbed the leaderboard.
    /app/kiosk identifies each person instead. Old bookmarks land there.
    """
    return RedirectResponse(url="kiosk", status_code=307)


@router.get("/enroll", include_in_schema=False)
def enroll_page():
    """Self-enrolment, reachable without the lab password (see below)."""
    return index()


@router.get("/kiosk", include_in_schema=False)
def kiosk_page():
    """Same page as /; the script reads the URL and becomes kiosk mode.

    The walk-up terminal for the lab: big Check in / Check out buttons,
    each press identifies whoever is standing there by face (the same
    models the physical kiosk uses) and credits *them*, then the screen
    returns to idle so the next person cannot inherit the session.
    """
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
    if not lab_password():
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

    if not web_auth.password_matches(req.password, lab_password()):
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


@router.post("/api/forget")
def forget(creditbot_session: str | None = Cookie(default=None)):
    """Forget who the last person was, staying signed in as a terminal.

    The kiosk calls this after every check-in/out so the next person to
    walk up is never operating as the previous one.
    """
    require_session(creditbot_session)
    response = JSONResponse(content={"status": "ok"})
    set_session_cookie(response, {})
    return response


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
def face_status(request: Request,
                creditbot_session: str | None = Cookie(default=None)):
    try:
        require_session(creditbot_session)
    except HTTPException:
        require_local(request)  # the password-free enrol page asks too
    return {"available": web_face.available()}


class FaceScanRequest(BaseModel):
    image_b64: str = Field(min_length=1, max_length=8_000_000)
    # Skip embedding and matching: just say where the face is. The kiosk
    # runs this fast for a box that keeps up, and the full pass rarely.
    detect_only: bool = False


class EnrollRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    samples: list[str] = Field(min_length=1, max_length=12)
    # Picked from the Discord member list. Without it the face is attached
    # to an invented id, and the credits earned here never meet the ones
    # earned on Discord.
    discord_id: str | None = Field(default=None, max_length=32)


def require_local(request: Request):
    """Refuse a password-free request that came in over the public internet.

    Tailscale marks Funnel (public) traffic with Tailscale-Funnel-Request;
    LAN and tailnet requests do not carry it.
    """
    if WEB_ENROLL_PUBLIC:
        return
    if request.headers.get("tailscale-funnel-request"):
        raise HTTPException(
            status_code=403,
            detail="Enrolment is only available on the lab network. "
                   "Enrol at the lab terminal.")


@router.post("/api/face-scan")
def face_scan(req: FaceScanRequest, request: Request,
              creditbot_session: str | None = Cookie(default=None)):
    """Where the face is, and whose it is — one call per camera frame.

    Signs the matched person in, like /api/face-login, so the kiosk can
    act immediately. Reachable without the lab password only from the
    lab network, so the enrolment page can show its own preview box.
    """
    session = None
    try:
        session = require_session(creditbot_session)
    except HTTPException:
        require_local(request)  # unauthenticated preview, lab network only

    try:
        result = web_face.scan(req.image_b64, detect_only=req.detect_only)
    except web_face.FaceLoginError as e:
        raise HTTPException(status_code=e.status, detail=e.detail)

    if result["match"] and session is not None:
        match = result["match"]
        user = database.get_user(match["discord_id"])
        name = user["username"] if user else match["name"]
        match["name"] = name
        response = JSONResponse(content=result)
        set_session_cookie(response, {"discord_id": match["discord_id"],
                                      "name": name})
        return response
    return result


@router.get("/api/discord/members")
def discord_members(q: str = "", request: Request = None):
    """The Discord server's members, so a newcomer can pick themselves.

    Password-free like enrolment itself, and limited to the lab network
    the same way. If Discord cannot answer — no token, no GUILD_ID, a
    revoked token — that is reported rather than raised, so the page can
    fall back to typing a name.
    """
    require_local(request)
    if not discord_lookup.available():
        return {"available": False, "members": [],
                "detail": "Discord is not hooked up on this server yet."}
    try:
        members = (discord_lookup.search_members(q) if q.strip()
                   else discord_lookup.list_members())
    except discord_lookup.DiscordUnavailable as e:
        return {"available": False, "members": [], "detail": str(e)}

    known = {str(u["discord_id"]) for u in database.get_all_users()}
    enrolled = {str(r["discord_id"]) for r in database.get_face_encodings()}
    for m in members:
        m["known"] = str(m["discord_id"]) in known
        m["enrolled"] = str(m["discord_id"]) in enrolled
    return {"available": True, "members": members, "detail": None}


@router.post("/api/enroll")
def enroll(req: EnrollRequest, request: Request):
    """Add a new member and their face, with no password.

    Deliberately open on the lab network: someone new walks up to the
    terminal, types their name, and looks at the camera. Every sample
    that contains a usable face is stored; the member is created so they
    can check in straight away.
    """
    if not WEB_ENABLED:
        raise HTTPException(status_code=404, detail="Web client is disabled")
    require_local(request)

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="A name is required")

    discord_id = (req.discord_id or "").strip()
    if discord_id:
        # Re-enrolling a known account is fine: it adds face samples.
        if database.get_face_encodings():
            already = any(str(r["discord_id"]) == discord_id
                          for r in database.get_face_encodings())
            if already:
                raise HTTPException(
                    status_code=409,
                    detail=f"{name} is already enrolled — check in instead.")
    else:
        taken = any(u["username"].strip().lower() == name.lower()
                    for u in database.get_all_users())
        if taken:
            raise HTTPException(
                status_code=409,
                detail=f"{name} is already enrolled — check in instead.")

    embeddings = []
    last_detail = "No usable face in any of those frames."
    for sample in req.samples:
        try:
            embeddings.append(web_face.embed_face(sample))
        except web_face.FaceLoginError as e:
            last_detail = e.detail
            continue
    if not embeddings:
        raise HTTPException(status_code=422, detail=last_detail)

    if not discord_id:
        discord_id = "web:" + secrets.token_hex(8)
    database.get_or_create_user(discord_id, name)
    database.update_username(discord_id, name)
    for embedding in embeddings:
        database.save_face_encoding(discord_id, name, json.dumps(embedding))
    print(f"🌐 Web enrolment: {name} ({discord_id}), {len(embeddings)} samples")
    return {"status": "enrolled", "name": name, "discord_id": discord_id,
            "samples": len(embeddings),
            "linked_to_discord": not discord_id.startswith("web:")}


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

class PhotoRequest(BaseModel):
    # The kiosk sends the frame it recognized the person in, so the bot
    # can post it to the check-in channel the way the physical kiosk does.
    # Sent on the way out as well as the way in.
    photo_b64: str | None = Field(default=None, max_length=8_000_000)


CheckinRequest = PhotoRequest


def _thumbnail(photo: bytes, width: int = 220) -> bytes:
    """Shrink a check-in photo for the timeline. Falls back to the original."""
    try:
        import cv2
        import numpy as np
        frame = cv2.imdecode(np.frombuffer(photo, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            return photo
        h, w = frame.shape[:2]
        if w > width:
            frame = cv2.resize(frame, (width, max(1, round(h * width / w))),
                               interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return buf.tobytes() if ok else photo
    except Exception:
        return photo   # OpenCV missing or unhappy: keep the full frame


def _record_event(discord_id: str, name: str, action: str, result: dict,
                  photo_b64: str | None = None):
    """Add this check-in/out to the lab timeline. Never fatal."""
    try:
        thumb = None
        if photo_b64:
            thumb = _thumbnail(base64.b64decode(photo_b64, validate=True))
        database.add_checkin_event(
            discord_id, name, action,
            credits=int(result.get("credits_earned") or 0),
            bonuses=json.dumps(result.get("bonuses", [])),
            photo=thumb,
        )
    except Exception as e:
        print(f"⚠️ Could not record timeline event: {e}")


def _queue_photo(discord_id: str, name: str, photo_b64: str, result: dict,
                 action: str = "in"):
    """Save a kiosk photo for the bot to post. Never fatal to the check-in."""
    try:
        photo = base64.b64decode(photo_b64, validate=True)
        os.makedirs(config.KIOSK_UPLOADS_DIR, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        path = os.path.join(config.KIOSK_UPLOADS_DIR, f"{discord_id}_{stamp}.jpg")
        with open(path, "wb") as f:
            f.write(photo)
        database.add_kiosk_photo(discord_id, name, path,
                                 bonuses=json.dumps(result.get("bonuses", [])),
                                 action=action)
        return True
    except Exception as e:
        print(f"⚠️ Could not queue web check-in photo: {e}")
        return False


@router.post("/api/checkin")
def checkin(req: CheckinRequest | None = None,
            creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id, name = require_person(session)
    result = checkin_logic.perform_checkin(discord_id, name, source="web")
    print(f"🌐 Web check-in: {name} -> {result['status']}")
    photo_b64 = req.photo_b64 if req else None
    if result["status"] == "checked_in":
        _record_event(discord_id, name, "in", result, photo_b64)
        if photo_b64 and config.KIOSK_POST_PHOTOS:
            result["photo_queued"] = _queue_photo(discord_id, name, photo_b64, result)
    return result


@router.post("/api/checkout")
def checkout(req: PhotoRequest | None = None,
             creditbot_session: str | None = Cookie(default=None)):
    session = require_session(creditbot_session)
    discord_id, name = require_person(session)
    result = checkin_logic.perform_checkout(discord_id)
    print(f"🌐 Web check-out: {name} -> {result['status']}")
    photo_b64 = req.photo_b64 if req else None
    if result["status"] == "checked_out":
        _record_event(discord_id, name, "out", result, photo_b64)
        if photo_b64 and config.KIOSK_POST_PHOTOS:
            result["photo_queued"] = _queue_photo(discord_id, name, photo_b64,
                                                  result, action="out")
    return result


# ----------------------------------------------------------------- views

@router.get("/api/timeline")
def timeline(limit: int = 12,
             creditbot_session: str | None = Cookie(default=None)):
    """Who has come and gone lately, newest first."""
    require_session(creditbot_session)
    events = []
    for row in database.get_checkin_events(max(1, min(limit, 50))):
        try:
            bonuses = json.loads(row["bonuses"] or "[]")
        except ValueError:
            bonuses = []
        events.append({
            "id": row["id"],
            "name": row["username"],
            "action": row["action"],
            "credits": row["credits"],
            "bonuses": bonuses,
            "at": row["created_at"],
            "has_photo": bool(row["has_photo"]),
        })
    return {"events": events}


@router.get("/api/timeline/{event_id}/photo", include_in_schema=False)
def timeline_photo(event_id: int,
                   creditbot_session: str | None = Cookie(default=None)):
    require_session(creditbot_session)
    photo = database.get_checkin_event_photo(event_id)
    if not photo:
        raise HTTPException(status_code=404, detail="No photo for that moment")
    # These never change once written, so let the browser keep them.
    return Response(content=photo, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=86400"})


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

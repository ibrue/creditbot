"""Admin page for the web client: Discord hookup GUI + live terminal.

Served at /app/admin. Signing in requires WEB_ADMIN_PASSWORD (or, if
that is unset, the regular lab password) and marks the session cookie
admin=True; every /api/admin/* endpoint checks that flag.

The Discord flow is: paste the bot token -> Test (verifies it against
Discord and lists the servers the bot is in) -> pick the server ->
channels load into dropdowns -> Save. Values are written through
config.save_settings(), which stores them next to the database and
applies them to the running API immediately; the bot container reads the
same file when it (re)starts.
"""
import os

import requests
from fastapi import APIRouter, Cookie, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

import config
import web_auth
import web_face
import webapp
import weblog

DISCORD_API = "https://discord.com/api/v10"
TOKEN_PLACEHOLDER = "your-bot-token-here"

WEB_ADMIN_PASSWORD = os.getenv("WEB_ADMIN_PASSWORD", "")

ADMIN_FILE = os.path.join(webapp.WEB_DIR, "admin.html")

router = APIRouter()


def _token_configured() -> bool:
    return bool(config.DISCORD_TOKEN) and config.DISCORD_TOKEN != TOKEN_PLACEHOLDER


def require_admin(token: str | None) -> dict:
    session = webapp.require_session(token)
    if not session.get("admin"):
        raise HTTPException(status_code=403, detail="Admin sign-in required")
    return session


# ------------------------------------------------------------- the page

@router.get("/admin", include_in_schema=False)
def admin_page():
    if not webapp.WEB_ENABLED:
        raise HTTPException(status_code=404, detail="Web client is disabled")
    if not os.path.exists(ADMIN_FILE):
        raise HTTPException(status_code=500, detail="Admin page files are missing")
    return FileResponse(ADMIN_FILE, media_type="text/html")


# ----------------------------------------------------------- admin login

class AdminLoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


@router.post("/api/admin/login")
def admin_login(req: AdminLoginRequest, request: Request,
                creditbot_session: str | None = Cookie(default=None)):
    if not webapp.WEB_ENABLED:
        raise HTTPException(status_code=404, detail="Web client is disabled")
    expected = WEB_ADMIN_PASSWORD or webapp.WEB_PASSWORD
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Neither WEB_ADMIN_PASSWORD nor WEB_PASSWORD is set on "
                   "the server — the admin page cannot be used until one is.")

    # Same limiter as the regular login: admin login is a password too.
    key = webapp.client_key(request)
    allowed, retry_after = webapp.limiter.check(key)
    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"detail": f"Too many attempts. Try again in "
                               f"{retry_after // 60 + 1} minute(s)."},
            headers={"Retry-After": str(retry_after)},
        )

    if not web_auth.password_matches(req.password, expected):
        webapp.limiter.record_failure(key)
        raise HTTPException(status_code=401, detail="Wrong password")

    webapp.limiter.reset(key)
    # Keep any existing identity so admin work doesn't sign the person out.
    session = webapp.current_session(creditbot_session) or {}
    payload = {k: session[k] for k in ("discord_id", "name", "station")
               if k in session}
    payload["admin"] = True
    response = JSONResponse(content={"status": "ok"})
    webapp.set_session_cookie(response, payload)
    return response


# -------------------------------------------------------------- settings

@router.get("/api/admin/config")
def get_config(creditbot_session: str | None = Cookie(default=None)):
    require_admin(creditbot_session)
    token = config.DISCORD_TOKEN if _token_configured() else ""
    return {
        "discord": {
            "token_set": bool(token),
            "token_tail": token[-4:] if token else "",
            "guild_id": str(config.GUILD_ID or ""),
            "checkin_channel_id": str(config.CHECKIN_CHANNEL_ID or ""),
            "announcements_channel_id": str(config.ANNOUNCEMENTS_CHANNEL_ID or ""),
            "memes_channel_id": str(config.MEMES_CHANNEL_ID or ""),
            "notebooking_channel_id": str(config.NOTEBOOKING_CHANNEL_ID or ""),
        },
        "face_login_available": web_face.available(),
        "station": {"id": webapp.WEB_STATION_ID,
                    "name": webapp.WEB_STATION_NAME},
        "settings_path": config.SETTINGS_PATH,
    }


class SaveConfigRequest(BaseModel):
    # None = leave alone; "" = clear the saved override; value = set it.
    discord_token: str | None = Field(default=None, max_length=100)
    guild_id: str | None = Field(default=None, max_length=32)
    checkin_channel_id: str | None = Field(default=None, max_length=32)
    announcements_channel_id: str | None = Field(default=None, max_length=32)
    memes_channel_id: str | None = Field(default=None, max_length=32)
    notebooking_channel_id: str | None = Field(default=None, max_length=32)


@router.post("/api/admin/config")
def save_config(req: SaveConfigRequest,
                creditbot_session: str | None = Cookie(default=None)):
    require_admin(creditbot_session)

    numeric = {
        "GUILD_ID": req.guild_id,
        "CHECKIN_CHANNEL_ID": req.checkin_channel_id,
        "ANNOUNCEMENTS_CHANNEL_ID": req.announcements_channel_id,
        "MEMES_CHANNEL_ID": req.memes_channel_id,
        "NOTEBOOKING_CHANNEL_ID": req.notebooking_channel_id,
    }
    for key, value in numeric.items():
        if value is not None and value.strip() and not value.strip().isdigit():
            raise HTTPException(status_code=400,
                                detail=f"{key} must be a numeric Discord ID")

    updates = dict(numeric)
    if req.discord_token is not None:
        updates["DISCORD_TOKEN"] = req.discord_token
    config.save_settings(updates)
    print("⚙️ Admin page saved Discord settings")
    return {
        "status": "ok",
        "restart_needed": True,
        "detail": "Saved. The web/kiosk API uses the new values right away; "
                  "restart the bot container so the Discord bot does too "
                  "(on the NAS: docker compose restart bot).",
    }


# -------------------------------------------------- talking to Discord

def _discord_get(token: str, path: str):
    try:
        r = requests.get(f"{DISCORD_API}{path}",
                         headers={"Authorization": f"Bot {token}"},
                         timeout=10)
    except requests.RequestException as e:
        raise HTTPException(status_code=502,
                            detail=f"Could not reach Discord: {e}")
    if r.status_code == 401:
        raise HTTPException(status_code=401,
                            detail="Discord rejected the token")
    if r.status_code == 403:
        raise HTTPException(status_code=403,
                            detail="The bot lacks access — is it invited "
                                   "to that server?")
    if r.status_code >= 400:
        raise HTTPException(status_code=502,
                            detail=f"Discord error {r.status_code}")
    return r.json()


class DiscordTestRequest(BaseModel):
    # Blank = test the token already saved on the server.
    token: str | None = Field(default=None, max_length=100)


def _pick_token(supplied: str | None) -> str:
    token = (supplied or "").strip()
    if not token:
        if not _token_configured():
            raise HTTPException(status_code=400,
                                detail="No token — paste one first")
        token = config.DISCORD_TOKEN
    return token


@router.post("/api/admin/discord/test")
def discord_test(req: DiscordTestRequest,
                 creditbot_session: str | None = Cookie(default=None)):
    require_admin(creditbot_session)
    token = _pick_token(req.token)
    me = _discord_get(token, "/users/@me")
    guilds = _discord_get(token, "/users/@me/guilds")
    return {
        "bot": {"id": me.get("id"), "username": me.get("username")},
        "guilds": [{"id": g.get("id"), "name": g.get("name")}
                   for g in guilds],
    }


class ChannelsRequest(BaseModel):
    guild_id: str = Field(min_length=1, max_length=32)
    token: str | None = Field(default=None, max_length=100)


@router.post("/api/admin/discord/channels")
def discord_channels(req: ChannelsRequest,
                     creditbot_session: str | None = Cookie(default=None)):
    require_admin(creditbot_session)
    if not req.guild_id.strip().isdigit():
        raise HTTPException(status_code=400, detail="guild_id must be numeric")
    token = _pick_token(req.token)
    channels = _discord_get(token, f"/guilds/{req.guild_id.strip()}/channels")
    text = [{"id": c.get("id"), "name": c.get("name")}
            for c in channels if c.get("type") in (0, 5)]  # text + announcement
    text.sort(key=lambda c: (c["name"] or ""))
    return {"channels": text}


# --------------------------------------------------------------- terminal

@router.get("/api/admin/logs")
def logs(since: int = 0,
         creditbot_session: str | None = Cookie(default=None)):
    require_admin(creditbot_session)
    lines = weblog.since(since)
    return {"lines": lines, "next": lines[-1]["n"] if lines else since}

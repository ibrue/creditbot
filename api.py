"""Kiosk API for the social credit system.

A small HTTP API that runs alongside the Discord bot (same SQLite
database) so a check-in kiosk — e.g. the facial-recognition GUI in
kiosk/ — can check members in and out with the same credit rules.

Run:  uvicorn api:app --host 0.0.0.0 --port 8765

Auth: every request (except /health) must send the header
      X-API-Key: <KIOSK_API_KEY from the environment>
"""
import base64
import json
import os
import secrets
from datetime import datetime

import requests
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

import checkin_logic
import config
import database

API_KEY = os.getenv("KIOSK_API_KEY", "")
DISCORD_API = "https://discord.com/api/v10"

app = FastAPI(title="Social Credit Kiosk API", version="1.0.0")

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.on_event("startup")
def startup():
    database.init_database()
    if not API_KEY:
        print("⚠️  KIOSK_API_KEY is not set — all kiosk requests will be rejected.")
        print("   Set KIOSK_API_KEY in your .env file (any long random string).")


def require_api_key(key: str = Security(api_key_header)):
    if not API_KEY or not key or not secrets.compare_digest(key, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")


class CheckinRequest(BaseModel):
    discord_id: str = Field(min_length=1, max_length=32)
    username: str = Field(min_length=1, max_length=100)
    # Optional check-in photo (JPEG, base64) — queued for the bot to post
    # to the check-in channel
    photo_b64: str | None = Field(default=None, max_length=8_000_000)


class CheckoutRequest(BaseModel):
    discord_id: str = Field(min_length=1, max_length=32)


class FaceEnrollRequest(BaseModel):
    discord_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=100)
    embedding: list[float] = Field(min_length=8, max_length=1024)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/members", dependencies=[Depends(require_api_key)])
def members():
    return {"members": database.get_all_users()}


@app.get("/checked-in", dependencies=[Depends(require_api_key)])
def checked_in():
    return {"checked_in": database.get_all_checked_in()}


@app.post("/checkin", dependencies=[Depends(require_api_key)])
def checkin(req: CheckinRequest):
    result = checkin_logic.perform_checkin(req.discord_id, req.username, source="kiosk")
    print(f"🖥️ Kiosk check-in: {req.username} -> {result['status']}")

    # Queue the check-in photo for the bot to post in the check-in channel
    if result["status"] == "checked_in" and req.photo_b64 and config.KIOSK_POST_PHOTOS:
        try:
            photo = base64.b64decode(req.photo_b64, validate=True)
            os.makedirs(config.KIOSK_UPLOADS_DIR, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            path = os.path.join(config.KIOSK_UPLOADS_DIR,
                                f"{req.discord_id}_{stamp}.jpg")
            with open(path, "wb") as f:
                f.write(photo)
            database.add_kiosk_photo(
                req.discord_id, req.username, path,
                bonuses=json.dumps(result.get("bonuses", []))
            )
            result["photo_queued"] = True
        except Exception as e:
            print(f"⚠️ Could not queue check-in photo: {e}")
            result["photo_queued"] = False

    return result


@app.post("/checkout", dependencies=[Depends(require_api_key)])
def checkout(req: CheckoutRequest):
    result = checkin_logic.perform_checkout(req.discord_id)
    print(f"🖥️ Kiosk check-out: {req.discord_id} -> {result['status']}")
    return result


@app.get("/status/{discord_id}", dependencies=[Depends(require_api_key)])
def status(discord_id: str):
    active = database.get_active_checkin(discord_id)
    user = database.get_user(discord_id)
    return {
        "checked_in": active is not None,
        "checkin_time": active["checkin_time"] if active else None,
        "total_credits": user["total_credits"] if user else 0,
    }


class AddMemberRequest(BaseModel):
    discord_id: str = Field(min_length=1, max_length=32)
    username: str = Field(min_length=1, max_length=100)


def _discord_get(path: str, params: dict | None = None):
    """Call the Discord REST API with the bot token."""
    if not config.DISCORD_TOKEN or config.DISCORD_TOKEN == "your-bot-token-here":
        raise HTTPException(status_code=503, detail="DISCORD_TOKEN not configured")
    r = requests.get(
        f"{DISCORD_API}{path}",
        headers={"Authorization": f"Bot {config.DISCORD_TOKEN}"},
        params=params,
        timeout=10,
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="Not found on Discord")
    r.raise_for_status()
    return r.json()


def _avatar_url(user: dict) -> str | None:
    if user.get("avatar"):
        return f"https://cdn.discordapp.com/avatars/{user['id']}/{user['avatar']}.png?size=128"
    return None


@app.get("/discord/search", dependencies=[Depends(require_api_key)])
def discord_search(q: str):
    """Search members of the configured Discord server by name."""
    if config.GUILD_ID == 0:
        raise HTTPException(
            status_code=503,
            detail="GUILD_ID not set in the server .env — needed for Discord search"
        )
    members = _discord_get(
        f"/guilds/{config.GUILD_ID}/members/search",
        params={"query": q, "limit": 10},
    )
    results = []
    for m in members:
        user = m.get("user", {})
        if user.get("bot"):
            continue
        results.append({
            "discord_id": user.get("id"),
            "username": user.get("username"),
            "display_name": m.get("nick") or user.get("global_name") or user.get("username"),
            "avatar_url": _avatar_url(user),
        })
    return {"results": results}


@app.get("/discord/user/{discord_id}", dependencies=[Depends(require_api_key)])
def discord_user(discord_id: str):
    """Pull a Discord user's profile by ID."""
    user = _discord_get(f"/users/{discord_id}")
    return {
        "discord_id": user.get("id"),
        "username": user.get("username"),
        "display_name": user.get("global_name") or user.get("username"),
        "avatar_url": _avatar_url(user),
    }


@app.post("/members", dependencies=[Depends(require_api_key)])
def add_member(req: AddMemberRequest):
    """Add a person to the credit system (or update their username)."""
    user = database.get_or_create_user(req.discord_id, req.username)
    database.update_username(req.discord_id, req.username)
    print(f"🖥️ Kiosk member add/link: {req.username} ({req.discord_id})")
    return {
        "status": "ok",
        "discord_id": req.discord_id,
        "username": req.username,
        "total_credits": user["total_credits"],
    }


@app.get("/faces", dependencies=[Depends(require_api_key)])
def get_faces():
    faces = []
    for row in database.get_face_encodings():
        faces.append({
            "id": row["id"],
            "discord_id": row["discord_id"],
            "name": row["name"],
            "embedding": json.loads(row["embedding"]),
        })
    return {"faces": faces}


@app.post("/faces", dependencies=[Depends(require_api_key)])
def enroll_face(req: FaceEnrollRequest):
    row_id = database.save_face_encoding(
        req.discord_id, req.name, json.dumps(req.embedding)
    )
    # Make sure they exist as a user so check-in works immediately
    database.get_or_create_user(req.discord_id, req.name)
    print(f"🖥️ Kiosk enrollment: {req.name} ({req.discord_id})")
    return {"status": "enrolled", "id": row_id}


@app.delete("/faces/{discord_id}", dependencies=[Depends(require_api_key)])
def delete_faces(discord_id: str):
    deleted = database.delete_face_encodings(discord_id)
    return {"status": "deleted", "count": deleted}

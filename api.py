"""Kiosk API for the social credit system.

A small HTTP API that runs alongside the Discord bot (same SQLite
database) so a check-in kiosk — e.g. the facial-recognition GUI in
kiosk/ — can check members in and out with the same credit rules.

Run:  uvicorn api:app --host 0.0.0.0 --port 8765

Auth: every request (except /health) must send the header
      X-API-Key: <KIOSK_API_KEY from the environment>
"""
import json
import os
import secrets

from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

import checkin_logic
import database

API_KEY = os.getenv("KIOSK_API_KEY", "")

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

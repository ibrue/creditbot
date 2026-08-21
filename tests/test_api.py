"""Tests for the kiosk HTTP API, including its authentication."""
import base64
import json

import pytest

import config


# ------------------------------------------------------------------ auth

def test_health_needs_no_key(client):
    client.headers.pop("X-API-Key")
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", [
    "/members", "/checked-in", "/faces", "/status/1", "/discord/search?q=x",
])
def test_get_endpoints_reject_a_missing_key(client, path):
    client.headers.pop("X-API-Key")
    assert client.get(path).status_code == 401


def test_wrong_key_is_rejected(client):
    client.headers["X-API-Key"] = "not-the-key"
    assert client.get("/members").status_code == 401


def test_post_endpoints_reject_a_missing_key(client):
    client.headers.pop("X-API-Key")
    assert client.post("/checkin", json={"discord_id": "1", "username": "a"}).status_code == 401
    assert client.post("/checkout", json={"discord_id": "1"}).status_code == 401
    assert client.delete("/faces/1").status_code == 401


def test_unset_server_key_rejects_everything(client, monkeypatch):
    """An unconfigured KIOSK_API_KEY must fail closed, not open."""
    import api

    monkeypatch.setattr(api, "API_KEY", "")
    client.headers["X-API-Key"] = ""
    assert client.get("/members").status_code == 401
    client.headers["X-API-Key"] = "anything"
    assert client.get("/members").status_code == 401


# --------------------------------------------------------------- members

def test_add_and_list_members(client):
    assert client.get("/members").json() == {"members": []}

    response = client.post("/members", json={"discord_id": "1", "username": "alice"})
    assert response.status_code == 200
    assert response.json()["total_credits"] == 0

    assert len(client.get("/members").json()["members"]) == 1


def test_adding_an_existing_member_updates_their_username(client):
    client.post("/members", json={"discord_id": "1", "username": "alice"})
    client.post("/members", json={"discord_id": "1", "username": "alice_v2"})

    members = client.get("/members").json()["members"]
    assert len(members) == 1
    assert members[0]["username"] == "alice_v2"


# -------------------------------------------------------------- check-in

def test_checkin_checkout_roundtrip(client):
    response = client.post("/checkin", json={"discord_id": "1", "username": "alice"})
    assert response.status_code == 200
    assert response.json()["status"] == "checked_in"

    assert client.get("/status/1").json()["checked_in"] is True
    assert len(client.get("/checked-in").json()["checked_in"]) == 1

    response = client.post("/checkout", json={"discord_id": "1"})
    assert response.json()["status"] == "checked_out"
    assert client.get("/status/1").json()["checked_in"] is False


def test_double_checkin_reports_already_checked_in(client):
    client.post("/checkin", json={"discord_id": "1", "username": "alice"})
    response = client.post("/checkin", json={"discord_id": "1", "username": "alice"})
    assert response.json()["status"] == "already_checked_in"


def test_checkout_without_checkin(client):
    response = client.post("/checkout", json={"discord_id": "1"})
    assert response.json()["status"] == "not_checked_in"


def test_status_of_unknown_user(client):
    body = client.get("/status/nobody").json()
    assert body == {"checked_in": False, "checkin_time": None, "total_credits": 0}


@pytest.mark.parametrize("payload", [
    {},
    {"discord_id": "1"},
    {"discord_id": "", "username": "alice"},
    {"discord_id": "1", "username": ""},
    {"discord_id": "x" * 33, "username": "alice"},
    {"discord_id": "1", "username": "x" * 101},
])
def test_malformed_checkin_payloads_are_rejected(client, payload):
    assert client.post("/checkin", json=payload).status_code == 422


# ---------------------------------------------------------------- photos

def _jpeg_b64():
    return base64.b64encode(b"\xff\xd8\xff\xe0fake-jpeg-bytes\xff\xd9").decode()


def test_checkin_photo_is_saved_and_queued(client, uploads_dir, db):
    response = client.post("/checkin", json={
        "discord_id": "1", "username": "alice", "photo_b64": _jpeg_b64(),
    })
    body = response.json()

    assert body["photo_queued"] is True
    assert len(list(uploads_dir.iterdir())) == 1

    queued = db.get_unposted_kiosk_photos()
    assert len(queued) == 1
    assert queued[0]["discord_id"] == "1"
    assert json.loads(queued[0]["bonuses"]) == body["bonuses"]


def test_a_corrupt_photo_does_not_fail_the_checkin(client, db):
    response = client.post("/checkin", json={
        "discord_id": "1", "username": "alice", "photo_b64": "!!!not-base64!!!",
    })

    assert response.status_code == 200
    assert response.json()["status"] == "checked_in"
    assert response.json()["photo_queued"] is False
    assert db.get_active_checkin("1") is not None


def test_no_photo_is_queued_when_posting_is_disabled(client, monkeypatch, db):
    monkeypatch.setattr(config, "KIOSK_POST_PHOTOS", False)

    body = client.post("/checkin", json={
        "discord_id": "1", "username": "alice", "photo_b64": _jpeg_b64(),
    }).json()

    assert "photo_queued" not in body
    assert db.get_unposted_kiosk_photos() == []


def test_no_photo_is_queued_for_a_duplicate_checkin(client, db):
    client.post("/checkin", json={"discord_id": "1", "username": "alice"})
    body = client.post("/checkin", json={
        "discord_id": "1", "username": "alice", "photo_b64": _jpeg_b64(),
    }).json()

    assert body["status"] == "already_checked_in"
    assert db.get_unposted_kiosk_photos() == []


def test_oversized_photo_is_rejected(client):
    response = client.post("/checkin", json={
        "discord_id": "1", "username": "alice", "photo_b64": "A" * 8_000_001,
    })
    assert response.status_code == 422


# ----------------------------------------------------------------- faces

def test_face_enrollment_roundtrip(client):
    embedding = [0.1] * 128
    response = client.post("/faces", json={
        "discord_id": "1", "name": "alice", "embedding": embedding,
    })
    assert response.status_code == 200
    assert response.json()["status"] == "enrolled"

    faces = client.get("/faces").json()["faces"]
    assert len(faces) == 1
    assert faces[0]["embedding"] == embedding

    assert client.delete("/faces/1").json()["count"] == 1
    assert client.get("/faces").json()["faces"] == []


def test_enrolling_a_face_creates_the_member(client):
    client.post("/faces", json={
        "discord_id": "1", "name": "alice", "embedding": [0.1] * 128,
    })
    assert len(client.get("/members").json()["members"]) == 1


@pytest.mark.parametrize("embedding", [[], [0.1] * 7, [0.1] * 1025])
def test_bad_embedding_sizes_are_rejected(client, embedding):
    response = client.post("/faces", json={
        "discord_id": "1", "name": "alice", "embedding": embedding,
    })
    assert response.status_code == 422


def test_deleting_faces_for_an_unknown_user_is_a_noop(client):
    assert client.delete("/faces/nobody").json()["count"] == 0


# --------------------------------------------------------------- discord

def test_discord_search_requires_a_guild_id(client, monkeypatch):
    monkeypatch.setattr(config, "GUILD_ID", 0)
    response = client.get("/discord/search?q=alice")
    assert response.status_code == 503
    assert "GUILD_ID" in response.json()["detail"]


def test_discord_lookup_requires_a_token(client, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_TOKEN", "your-bot-token-here")
    response = client.get("/discord/user/123")
    assert response.status_code == 503
    assert "DISCORD_TOKEN" in response.json()["detail"]

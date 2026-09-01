"""Tests for the admin page: admin login, the Discord setup endpoints,
saved settings, and the terminal log feed."""
import pytest
from fastapi.testclient import TestClient

import api
import config
import database
import web_auth
import webadmin
import webapp
import weblog

PASSWORD = "lab-password-123"
ADMIN_PASSWORD = "admin-password-456"


@pytest.fixture
def web(db, monkeypatch, tmp_path):
    monkeypatch.setattr(webapp, "WEB_ENABLED", True)
    monkeypatch.setattr(webapp, "WEB_PASSWORD", PASSWORD)
    monkeypatch.setattr(webapp, "WEB_HTTPS", False)
    monkeypatch.setattr(webapp, "_secret_cache", b"test-signing-secret")
    monkeypatch.setattr(webapp, "limiter", web_auth.LoginLimiter())
    monkeypatch.setattr(webadmin, "WEB_ADMIN_PASSWORD", ADMIN_PASSWORD)
    monkeypatch.setattr(config, "SETTINGS_PATH",
                        str(tmp_path / "settings_overrides.json"))
    monkeypatch.setattr(config, "_settings", {})
    # save_settings() rewrites these module globals; registering their
    # current values with monkeypatch restores them after each test.
    for key in ("DISCORD_TOKEN", "GUILD_ID", "CHECKIN_CHANNEL_ID",
                "ANNOUNCEMENTS_CHANNEL_ID", "MEMES_CHANNEL_ID",
                "NOTEBOOKING_CHANNEL_ID"):
        monkeypatch.setattr(config, key, getattr(config, key))
    with TestClient(api.app) as client:
        yield client


@pytest.fixture
def admin(web):
    response = web.post("/app/api/admin/login",
                        json={"password": ADMIN_PASSWORD})
    assert response.status_code == 200
    return web


# ------------------------------------------------------------ admin auth

def test_the_admin_page_is_served(web):
    response = web.get("/app/admin")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_admin_endpoints_reject_the_signed_out(web):
    web.cookies.clear()
    assert web.get("/app/api/admin/config").status_code == 401
    assert web.get("/app/api/admin/logs").status_code == 401
    assert web.post("/app/api/admin/config", json={}).status_code == 401


def test_a_lab_session_is_not_an_admin_session(web):
    """The lab password signs people in; only the admin password admins."""
    web.post("/app/api/login", json={"password": PASSWORD})
    assert web.get("/app/api/admin/config").status_code == 403
    assert web.get("/app/api/admin/logs").status_code == 403


def test_the_lab_password_does_not_open_admin_when_one_is_set(web):
    response = web.post("/app/api/admin/login", json={"password": PASSWORD})
    assert response.status_code == 401


def test_the_lab_password_is_the_fallback_admin_password(web, monkeypatch):
    monkeypatch.setattr(webadmin, "WEB_ADMIN_PASSWORD", "")
    response = web.post("/app/api/admin/login", json={"password": PASSWORD})
    assert response.status_code == 200
    assert web.get("/app/api/admin/config").status_code == 200


def test_admin_login_keeps_the_current_identity(db, web):
    import database
    database.get_or_create_user("1", "alice")
    web.post("/app/api/login", json={"password": PASSWORD})
    web.post("/app/api/select", json={"discord_id": "1"})
    web.post("/app/api/admin/login", json={"password": ADMIN_PASSWORD})
    me = web.get("/app/api/me").json()
    assert me["person"]["discord_id"] == "1"


def test_admin_login_is_rate_limited(web, monkeypatch):
    monkeypatch.setattr(webapp, "limiter",
                        web_auth.LoginLimiter(max_attempts=3, window_seconds=900))
    for _ in range(3):
        web.post("/app/api/admin/login", json={"password": "nope"})
    blocked = web.post("/app/api/admin/login", json={"password": "nope"})
    assert blocked.status_code == 429


# -------------------------------------------------------------- settings

def test_config_never_returns_the_token(admin, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_TOKEN", "very-secret-token-abcd")
    body = admin.get("/app/api/admin/config").json()
    assert "very-secret-token" not in str(body)
    assert body["discord"]["token_set"] is True
    assert body["discord"]["token_tail"] == "abcd"


def test_saving_settings_applies_and_persists(admin):
    response = admin.post("/app/api/admin/config", json={
        "discord_token": "new-token-xyz",
        "guild_id": "123",
        "checkin_channel_id": "456",
    })
    assert response.status_code == 200
    assert response.json()["restart_needed"] is True

    # Applied to the running process...
    assert config.DISCORD_TOKEN == "new-token-xyz"
    assert config.GUILD_ID == 123
    assert config.CHECKIN_CHANNEL_ID == 456
    # ...and persisted where the bot container will read it.
    assert config.load_settings()["DISCORD_TOKEN"] == "new-token-xyz"


def test_saving_a_blank_clears_the_override(admin):
    admin.post("/app/api/admin/config", json={"guild_id": "123"})
    admin.post("/app/api/admin/config", json={"guild_id": ""})
    assert "GUILD_ID" not in config.load_settings()


def test_non_numeric_ids_are_rejected(admin):
    response = admin.post("/app/api/admin/config",
                          json={"guild_id": "not-a-number"})
    assert response.status_code == 400


def test_discord_test_without_any_token_is_a_400(admin, monkeypatch):
    monkeypatch.setattr(config, "DISCORD_TOKEN", "your-bot-token-here")
    response = admin.post("/app/api/admin/discord/test", json={})
    assert response.status_code == 400


def test_discord_test_reports_the_bot_and_its_guilds(admin, monkeypatch):
    def fake_get(token, path):
        assert token == "tok-123"
        if path == "/users/@me":
            return {"id": "99", "username": "CreditBot"}
        if path == "/users/@me/guilds":
            return [{"id": "1", "name": "Robotics Lab", "extra": "ignored"}]
        raise AssertionError(path)
    monkeypatch.setattr(webadmin, "_discord_get", fake_get)

    body = admin.post("/app/api/admin/discord/test",
                      json={"token": "tok-123"}).json()
    assert body["bot"]["username"] == "CreditBot"
    assert body["guilds"] == [{"id": "1", "name": "Robotics Lab"}]


def test_channel_listing_keeps_only_text_channels(admin, monkeypatch):
    monkeypatch.setattr(webadmin, "_discord_get", lambda token, path: [
        {"id": "10", "name": "general", "type": 0},
        {"id": "11", "name": "voice-chat", "type": 2},
        {"id": "12", "name": "announcements", "type": 5},
    ])
    body = admin.post("/app/api/admin/discord/channels",
                      json={"guild_id": "1", "token": "tok"}).json()
    assert [c["id"] for c in body["channels"]] == ["12", "10"]


# --------------------------------------------------------------- terminal

def test_the_terminal_feed_is_incremental(admin):
    weblog.record("first line")
    first = admin.get("/app/api/admin/logs?since=0").json()
    assert any(l["line"] == "first line" for l in first["lines"])

    nothing = admin.get(f"/app/api/admin/logs?since={first['next']}").json()
    assert nothing["lines"] == []
    assert nothing["next"] == first["next"]

    weblog.record("second line")
    more = admin.get(f"/app/api/admin/logs?since={first['next']}").json()
    assert [l["line"] for l in more["lines"]] == ["second line"]


def test_prints_reach_the_terminal_feed(admin, db, monkeypatch):
    """A web check-in's print line must show up in the admin terminal.

    In production the tee installed at import wraps the real stdout;
    pytest swaps sys.stdout per test, so wrap its stream here the same
    way to exercise the same write->record path.
    """
    import sys
    monkeypatch.setattr(sys, "stdout", weblog._Tee(sys.stdout))
    admin.post("/app/api/login", json={"password": PASSWORD, "station": True})
    admin.post("/app/api/admin/login", json={"password": ADMIN_PASSWORD})
    admin.post("/app/api/checkin")
    lines = admin.get("/app/api/admin/logs?since=0").json()["lines"]
    assert any("Web check-in" in l["line"] for l in lines)


# ------------------------------------------------------------ diagnostics
# The page that answers "which camera is it using, and why did it not
# recognise me" without anyone having to SSH into the NAS.

def test_diagnostics_page_is_served(admin):
    response = admin.get("/app/admin/diagnostics")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_diagnostics_needs_an_admin_session(web):
    web.cookies.clear()
    assert web.get("/app/api/admin/diagnostics").status_code == 401


def test_a_plain_lab_session_is_not_enough(web):
    """Signing in with the lab password must not expose the config."""
    web.post("/app/api/login", json={"password": PASSWORD})
    assert web.get("/app/api/admin/diagnostics").status_code == 403


def test_diagnostics_reports_the_things_that_go_wrong(admin, db, monkeypatch):
    monkeypatch.setattr(webadmin.web_face, "available", lambda: True)
    database.get_or_create_user("42", "alice")
    database.save_face_encoding("42", "alice", "[0.1]")

    body = admin.get("/app/api/admin/diagnostics").json()
    assert body["face"]["models_loaded"] is True
    assert body["face"]["enrolled_people"] == 1
    assert body["face"]["face_samples"] == 1
    assert body["counts"]["members"] == 1
    assert "kiosk_camera" in body["settings"]
    assert "posts_photos_to_discord" in body["settings"]
    # Secrets are reported as booleans, never values.
    assert body["settings"]["lab_password_set"] in (True, False)
    assert "password" not in str(body["settings"].get("lab_password_set"))


def test_diagnostics_survives_discord_being_down(admin, db, monkeypatch):
    monkeypatch.setattr(webadmin.discord_lookup, "token_configured", lambda: True)

    def boom(*_a, **_k):
        raise webadmin.discord_lookup.DiscordUnavailable("token revoked")
    monkeypatch.setattr(webadmin.discord_lookup, "_get", boom)

    body = admin.get("/app/api/admin/diagnostics").json()
    assert body["discord"]["connected"] is False
    assert "revoked" in body["discord"]["detail"]


def test_the_preferred_camera_round_trips(admin, db):
    assert admin.post("/app/api/admin/config",
                      json={"kiosk_camera": "BRIO"}).status_code == 200
    assert config.KIOSK_CAMERA == "BRIO"
    assert admin.get("/app/api/admin/config").json()["kiosk_camera"] == "BRIO"


# ------------------------------------------------------ rotating passwords
# "Change the door key when someone leaves the team" was advice the README
# gave but the software could not follow without a shell on the server.

def test_the_lab_password_can_be_rotated_from_the_admin_page(admin, web, db):
    assert admin.post("/app/api/admin/config",
                      json={"lab_password": "a-brand-new-key"}).status_code == 200

    # The new one works immediately, with no restart...
    assert webapp.lab_password() == "a-brand-new-key"
    fresh = TestClient(api.app)
    assert fresh.post("/app/api/login",
                      json={"password": "a-brand-new-key"}).status_code == 200
    # ...and the old one does not.
    assert fresh.post("/app/api/login",
                      json={"password": PASSWORD}).status_code == 401


def test_rotating_the_password_does_not_sign_a_terminal_out(admin, web, db):
    """An armed kiosk must not need re-arming because the key changed."""
    database.get_or_create_user("42", "alice")
    web.post("/app/api/login", json={"password": PASSWORD})
    web.post("/app/api/select", json={"discord_id": "42"})
    assert web.get("/app/api/me").json()["person"]["name"] == "alice"

    admin.post("/app/api/admin/config", json={"lab_password": "a-brand-new-key"})

    # Cookies are signed with WEB_SECRET, not the password.
    assert web.get("/app/api/me").json()["person"]["name"] == "alice"


def test_the_admin_password_can_be_rotated_separately(admin, db):
    assert admin.post("/app/api/admin/config",
                      json={"admin_password": "separate-admin-key"}).status_code == 200
    assert webadmin.admin_password() == "separate-admin-key"
    # The lab password is untouched, and no longer opens the admin page.
    assert webapp.lab_password() == PASSWORD
    fresh = TestClient(api.app)
    assert fresh.post("/app/api/admin/login",
                      json={"password": PASSWORD}).status_code == 401
    assert fresh.post("/app/api/admin/login",
                      json={"password": "separate-admin-key"}).status_code == 200


def test_passwords_are_never_read_back_out(admin, db):
    admin.post("/app/api/admin/config", json={"lab_password": "a-brand-new-key"})
    body = admin.get("/app/api/admin/config").json()
    assert "a-brand-new-key" not in str(body)
    diag = admin.get("/app/api/admin/diagnostics").json()
    assert "a-brand-new-key" not in str(diag)
    assert diag["settings"]["lab_password_set"] is True   # only ever a boolean


def test_a_trivially_short_password_is_refused(admin, db):
    assert admin.post("/app/api/admin/config",
                      json={"lab_password": "short"}).status_code == 422
    assert webapp.lab_password() == PASSWORD   # unchanged


def test_rotating_needs_admin_not_just_the_lab_password(web, db):
    web.post("/app/api/login", json={"password": PASSWORD})
    assert web.post("/app/api/admin/config",
                    json={"lab_password": "a-brand-new-key"}).status_code == 403
    assert webapp.lab_password() == PASSWORD

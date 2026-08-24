"""Tests for the browser client's HTTP surface.

Everything behind the login is gated on a signed cookie, so most of these
check that an unauthenticated or forged request gets nowhere.
"""
import pytest
from fastapi.testclient import TestClient

import api
import database
import web_auth
import webapp

PASSWORD = "lab-password-123"

PROTECTED_GETS = ["/app/api/me", "/app/api/members", "/app/api/whos-in",
                  "/app/api/leaderboard", "/app/api/history"]
PROTECTED_POSTS = ["/app/api/checkin", "/app/api/checkout"]


@pytest.fixture
def web(db, monkeypatch, tmp_path):
    """A TestClient for the web client, signed out."""
    monkeypatch.setattr(webapp, "WEB_ENABLED", True)
    monkeypatch.setattr(webapp, "WEB_PASSWORD", PASSWORD)
    monkeypatch.setattr(webapp, "WEB_HTTPS", False)
    monkeypatch.setattr(webapp, "_secret_cache", b"test-signing-secret")
    monkeypatch.setattr(webapp, "limiter", web_auth.LoginLimiter())
    with TestClient(api.app) as client:
        yield client


@pytest.fixture
def signed_in(web):
    assert web.post("/app/api/login", json={"password": PASSWORD}).status_code == 200
    return web


@pytest.fixture
def alice(db, signed_in):
    database.get_or_create_user("1", "alice")
    signed_in.post("/app/api/select", json={"discord_id": "1"})
    return signed_in


# ------------------------------------------------------------- the page

def test_the_page_is_served(web):
    response = web.get("/app/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_the_page_needs_no_login(web):
    """The login form itself must be reachable while signed out."""
    web.cookies.clear()
    assert web.get("/app/").status_code == 200


def test_the_page_can_be_turned_off(web, monkeypatch):
    monkeypatch.setattr(webapp, "WEB_ENABLED", False)
    assert web.get("/app/").status_code == 404


# ---------------------------------------------------------------- login

def test_the_right_password_signs_you_in(web):
    assert web.post("/app/api/login", json={"password": PASSWORD}).status_code == 200
    assert web.get("/app/api/me").status_code == 200


def test_the_wrong_password_is_refused(web):
    assert web.post("/app/api/login", json={"password": "nope"}).status_code == 401
    assert web.get("/app/api/me").status_code == 401


def test_an_unset_server_password_refuses_everyone(web, monkeypatch):
    """Fail closed: an unconfigured server must not accept a blank password."""
    monkeypatch.setattr(webapp, "WEB_PASSWORD", "")

    assert web.post("/app/api/login", json={"password": ""}).status_code == 422
    assert web.post("/app/api/login", json={"password": "anything"}).status_code == 503


def test_login_is_rate_limited(web, monkeypatch):
    monkeypatch.setattr(webapp, "limiter",
                        web_auth.LoginLimiter(max_attempts=3, window_seconds=900))

    for _ in range(3):
        assert web.post("/app/api/login", json={"password": "nope"}).status_code == 401

    blocked = web.post("/app/api/login", json={"password": "nope"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_rate_limiting_also_blocks_the_right_password(web, monkeypatch):
    """Otherwise the limit would be trivial to sidestep by guessing on."""
    monkeypatch.setattr(webapp, "limiter",
                        web_auth.LoginLimiter(max_attempts=2, window_seconds=900))
    for _ in range(2):
        web.post("/app/api/login", json={"password": "nope"})

    assert web.post("/app/api/login", json={"password": PASSWORD}).status_code == 429


def test_signing_out_ends_the_session(signed_in):
    signed_in.post("/app/api/logout")
    assert signed_in.get("/app/api/me").status_code == 401


# ------------------------------------------------------------ the cookie

def test_the_session_cookie_is_not_readable_by_scripts(web):
    response = web.post("/app/api/login", json={"password": PASSWORD})
    header = response.headers["set-cookie"].lower()

    assert "httponly" in header
    assert "samesite=lax" in header


def test_the_cookie_is_marked_secure_over_https(web, monkeypatch):
    monkeypatch.setattr(webapp, "WEB_HTTPS", True)
    response = web.post("/app/api/login", json={"password": PASSWORD})

    assert "secure" in response.headers["set-cookie"].lower()


def test_a_forged_cookie_gets_nowhere(web):
    """Someone who guesses the cookie's shape still cannot sign it."""
    forged = web_auth.make_token({"discord_id": "1", "name": "alice"},
                                 b"not-the-servers-secret")
    web.cookies.set(web_auth.SESSION_COOKIE, forged)

    assert web.get("/app/api/me").status_code == 401


def test_an_expired_cookie_gets_nowhere(web):
    stale = web_auth.make_token({"discord_id": "1"}, b"test-signing-secret",
                                ttl_hours=-1)
    web.cookies.set(web_auth.SESSION_COOKIE, stale)

    assert web.get("/app/api/me").status_code == 401


@pytest.mark.parametrize("path", PROTECTED_GETS)
def test_reads_require_a_session(web, path):
    assert web.get(path).status_code == 401


@pytest.mark.parametrize("path", PROTECTED_POSTS)
def test_writes_require_a_session(web, path):
    assert web.post(path).status_code == 401


# ------------------------------------------------------------- identity

def test_a_fresh_session_has_nobody_selected(signed_in):
    assert signed_in.get("/app/api/me").json() == {"signed_in": True, "person": None}


def test_selecting_a_member(alice):
    person = alice.get("/app/api/me").json()["person"]

    assert person["name"] == "alice"
    assert person["checked_in"] is False
    assert person["tier"]


def test_selecting_someone_who_does_not_exist(signed_in):
    assert signed_in.post("/app/api/select",
                          json={"discord_id": "999"}).status_code == 404


def test_you_must_pick_someone_before_checking_in(signed_in):
    assert signed_in.post("/app/api/checkin").status_code == 409
    assert signed_in.post("/app/api/checkout").status_code == 409
    assert signed_in.get("/app/api/history").status_code == 409


def test_a_deleted_member_reads_as_nobody_selected(alice, db):
    conn = db.get_connection()
    conn.execute("DELETE FROM users WHERE discord_id = '1'")
    conn.commit()
    conn.close()

    assert alice.get("/app/api/me").json()["person"] is None


# ------------------------------------------------------------ check in/out

def test_check_in_and_out(alice, db):
    checked_in = alice.post("/app/api/checkin").json()
    assert checked_in["status"] == "checked_in"
    assert alice.get("/app/api/me").json()["person"]["checked_in"] is True

    checked_out = alice.post("/app/api/checkout").json()
    assert checked_out["status"] == "checked_out"
    assert alice.get("/app/api/me").json()["person"]["checked_in"] is False


def test_a_second_check_in_is_reported_not_repaid(alice, db):
    alice.post("/app/api/checkin")
    credits = db.get_user("1")["total_credits"]

    assert alice.post("/app/api/checkin").json()["status"] == "already_checked_in"
    assert db.get_user("1")["total_credits"] == credits


def test_checking_out_without_checking_in(alice):
    assert alice.post("/app/api/checkout").json()["status"] == "not_checked_in"


def test_a_web_check_in_is_recorded_as_such(alice, db):
    """Its source is distinguishable from a kiosk or Discord check-in."""
    alice.post("/app/api/checkin")

    conn = db.get_connection()
    row = conn.execute(
        "SELECT message_id FROM checkins WHERE discord_id = '1'").fetchone()
    conn.close()
    assert row["message_id"] == "web"


def test_a_web_check_in_earns_the_same_as_the_kiosk(alice, db, monkeypatch):
    import checkin_logic
    monkeypatch.setattr(checkin_logic, "is_night_owl_time", lambda hour: False)

    alice.post("/app/api/checkin")
    web_credits = db.get_user("1")["total_credits"]

    conn = db.get_connection()
    for table in ("users", "checkins", "transactions", "streak_bonus"):
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    checkin_logic.perform_checkin("2", "bob", source="kiosk")
    assert db.get_user("2")["total_credits"] == web_credits


# ----------------------------------------------------------------- views

def test_whos_in_lists_people_with_durations(alice, db):
    alice.post("/app/api/checkin")
    people = alice.get("/app/api/whos-in").json()["checked_in"]

    assert len(people) == 1
    assert people[0]["name"] == "alice"
    assert "minute" in people[0]["duration"]


def test_whos_in_on_an_empty_lab(alice):
    assert alice.get("/app/api/whos-in").json()["checked_in"] == []


def test_the_leaderboard_has_both_boards(alice, db):
    db.add_credits("1", "alice", 10, "x")
    board = alice.get("/app/api/leaderboard").json()

    assert board["weekly"][0]["username"] == "alice"
    assert board["alltime"][0]["username"] == "alice"


def test_history_shows_your_credits(alice, db):
    db.add_credits("1", "alice", 5, "Being thanked")
    transactions = alice.get("/app/api/history").json()["transactions"]

    assert transactions[0]["reason"] == "Being thanked"
    assert transactions[0]["amount"] == 5


# ------------------------------------------------- the kiosk API is intact

def test_the_kiosk_api_still_answers(web):
    assert web.get("/health").json() == {"status": "ok"}


def test_a_web_session_does_not_open_the_kiosk_api(signed_in):
    """The two audiences are authenticated separately and must stay that way."""
    assert signed_in.get("/members").status_code == 401
    assert signed_in.get("/faces").status_code == 401


# ------------------------------------------------- rate-limit key handling

def test_a_spoofed_forwarded_header_cannot_reset_the_limit(web, monkeypatch):
    """Without a trusted proxy, X-Forwarded-For must be ignored — otherwise
    a caller invents a new value per request and never gets limited."""
    monkeypatch.setattr(webapp, "WEB_TRUST_PROXY", False)
    monkeypatch.setattr(webapp, "limiter",
                        web_auth.LoginLimiter(max_attempts=3, window_seconds=900))

    for i in range(3):
        web.post("/app/api/login", json={"password": "nope"},
                 headers={"X-Forwarded-For": f"10.0.0.{i}"})

    blocked = web.post("/app/api/login", json={"password": "nope"},
                       headers={"X-Forwarded-For": "10.0.0.99"})
    assert blocked.status_code == 429


def test_a_trusted_proxy_separates_real_clients(web, monkeypatch):
    """With a proxy in front, every caller would otherwise share one bucket
    and a single guesser could lock out the whole lab."""
    monkeypatch.setattr(webapp, "WEB_TRUST_PROXY", True)
    monkeypatch.setattr(webapp, "limiter",
                        web_auth.LoginLimiter(max_attempts=3, window_seconds=900))

    for _ in range(3):
        web.post("/app/api/login", json={"password": "nope"},
                 headers={"X-Forwarded-For": "10.0.0.1"})

    assert web.post("/app/api/login", json={"password": "nope"},
                    headers={"X-Forwarded-For": "10.0.0.1"}).status_code == 429
    assert web.post("/app/api/login", json={"password": "nope"},
                    headers={"X-Forwarded-For": "10.0.0.2"}).status_code == 401

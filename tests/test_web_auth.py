"""Tests for web session signing, passwords and login rate limiting.

The web client is protected by one shared password, so the parts that
limit the blast radius — unforgeable cookies, expiry, and a login limiter
— are the parts worth testing hardest.
"""
import json
import os
import sys
import time

import pytest

import web_auth


SECRET = b"a-test-signing-secret"


# ---------------------------------------------------------------- tokens

def test_token_round_trips():
    token = web_auth.make_token({"discord_id": "1", "name": "alice"}, SECRET)
    payload = web_auth.read_token(token, SECRET)

    assert payload["discord_id"] == "1"
    assert payload["name"] == "alice"


def test_a_token_carries_an_expiry():
    token = web_auth.make_token({}, SECRET, ttl_hours=2, now=1000.0)
    assert web_auth.read_token(token, SECRET, now=1000.0)["exp"] == 1000 + 7200


def test_an_expired_token_is_rejected():
    token = web_auth.make_token({"name": "alice"}, SECRET, ttl_hours=1, now=1000.0)

    assert web_auth.read_token(token, SECRET, now=1000.0) is not None
    assert web_auth.read_token(token, SECRET, now=1000.0 + 3601) is None


def test_expiry_is_exclusive_at_the_boundary():
    token = web_auth.make_token({}, SECRET, ttl_hours=1, now=1000.0)
    assert web_auth.read_token(token, SECRET, now=1000.0 + 3600) is None


def test_a_different_secret_cannot_read_the_token():
    """One lab's cookie must be worthless against another's server."""
    token = web_auth.make_token({"name": "alice"}, SECRET)
    assert web_auth.read_token(token, b"a-different-secret") is None


def test_a_tampered_payload_is_rejected():
    """The whole point: nobody can promote themselves by editing a cookie."""
    token = web_auth.make_token({"discord_id": "1", "name": "alice"}, SECRET)
    encoded, signature = token.split(".")

    forged_payload = web_auth._b64encode(
        json.dumps({"discord_id": "999", "name": "admin",
                    "exp": int(time.time()) + 9999}).encode())
    forged = f"{forged_payload}.{signature}"

    assert web_auth.read_token(forged, SECRET) is None


def test_a_tampered_signature_is_rejected():
    token = web_auth.make_token({"name": "alice"}, SECRET)
    encoded, signature = token.split(".")
    flipped = ("A" if signature[0] != "A" else "B") + signature[1:]

    assert web_auth.read_token(f"{encoded}.{flipped}", SECRET) is None


def test_an_unsigned_payload_is_rejected():
    """A bare base64 payload with no signature must not be accepted."""
    encoded = web_auth._b64encode(
        json.dumps({"name": "alice", "exp": int(time.time()) + 999}).encode())
    assert web_auth.read_token(encoded, SECRET) is None
    assert web_auth.read_token(f"{encoded}.", SECRET) is None


@pytest.mark.parametrize("token", [
    "", "garbage", "no.dots.here", "...", "a.b.c",
    "!!!.!!!", "." , None, 12345, [],
])
def test_malformed_tokens_are_rejected(token):
    assert web_auth.read_token(token, SECRET) is None


def test_a_payload_that_is_not_an_object_is_rejected():
    encoded = web_auth._b64encode(json.dumps([1, 2, 3]).encode())
    import hashlib, hmac
    signature = hmac.new(SECRET, encoded.encode(), hashlib.sha256).digest()
    token = f"{encoded}.{web_auth._b64encode(signature)}"

    assert web_auth.read_token(token, SECRET) is None


def test_a_token_without_an_expiry_is_rejected():
    """A hand-built cookie must not get an unlimited session."""
    import hashlib, hmac
    encoded = web_auth._b64encode(json.dumps({"name": "alice"}).encode())
    signature = hmac.new(SECRET, encoded.encode(), hashlib.sha256).digest()
    token = f"{encoded}.{web_auth._b64encode(signature)}"

    assert web_auth.read_token(token, SECRET) is None


# ------------------------------------------------------------- passwords

def test_the_right_password_matches():
    assert web_auth.password_matches("hunter2", "hunter2") is True


@pytest.mark.parametrize("supplied,expected", [
    ("wrong", "hunter2"),
    ("hunter", "hunter2"),
    ("hunter22", "hunter2"),
    ("HUNTER2", "hunter2"),
])
def test_a_wrong_password_does_not_match(supplied, expected):
    assert web_auth.password_matches(supplied, expected) is False


def test_an_unset_server_password_never_matches():
    """Failing closed matters: an unconfigured server must not let anyone in."""
    assert web_auth.password_matches("anything", "") is False
    assert web_auth.password_matches("", "") is False


def test_an_empty_supplied_password_never_matches():
    assert web_auth.password_matches("", "hunter2") is False


# ---------------------------------------------------------------- secret

def test_the_environment_secret_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("WEB_SECRET", "from-the-environment")
    assert web_auth.load_secret(str(tmp_path / ".web_secret")) == b"from-the-environment"


def test_a_secret_is_generated_and_kept(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_SECRET", raising=False)
    path = tmp_path / ".web_secret"

    first = web_auth.load_secret(str(path))
    assert path.exists()
    assert len(first) >= 32

    # Sessions must survive a restart, so the same secret comes back.
    assert web_auth.load_secret(str(path)) == first


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows has no POSIX mode bits — os.chmod only toggles the "
           "read-only flag there, so st_mode always reports 0o666. The "
           "server this protects runs on Linux; on Windows the file is "
           "covered by the user profile's ACLs instead.",
)
def test_the_stored_secret_is_not_world_readable(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_SECRET", raising=False)
    path = tmp_path / ".web_secret"
    web_auth.load_secret(str(path))

    assert (path.stat().st_mode & 0o077) == 0


def test_the_secret_is_written_on_every_platform(tmp_path, monkeypatch):
    """Whatever the permission model, the file itself must land and be read
    back — that is what makes sessions survive a restart."""
    monkeypatch.delenv("WEB_SECRET", raising=False)
    path = tmp_path / ".web_secret"

    secret = web_auth.load_secret(str(path))

    assert path.exists()
    assert path.read_text().strip().encode() == secret


def test_two_labs_get_different_secrets(tmp_path, monkeypatch):
    monkeypatch.delenv("WEB_SECRET", raising=False)
    one = web_auth.load_secret(str(tmp_path / "a" / ".web_secret"))
    two = web_auth.load_secret(str(tmp_path / "b" / ".web_secret"))

    assert one != two


def test_an_unwritable_location_still_yields_a_secret(tmp_path, monkeypatch):
    """A read-only disk should cost sessions on restart, not a crash."""
    monkeypatch.delenv("WEB_SECRET", raising=False)
    monkeypatch.setattr(web_auth.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("read-only")))

    secret = web_auth.load_secret(str(tmp_path / "nope" / ".web_secret"))
    assert len(secret) >= 32


# --------------------------------------------------------- rate limiting

def test_attempts_are_allowed_under_the_limit():
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for _ in range(2):
        assert limiter.check("1.2.3.4", now=1000.0)[0] is True
        limiter.record_failure("1.2.3.4", now=1000.0)

    assert limiter.check("1.2.3.4", now=1000.0)[0] is True


def test_the_limit_blocks_further_attempts():
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("1.2.3.4", now=1000.0)

    allowed, retry_after = limiter.check("1.2.3.4", now=1000.0)
    assert allowed is False
    assert 0 < retry_after <= 61


def test_the_window_slides():
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("1.2.3.4", now=1000.0)

    assert limiter.check("1.2.3.4", now=1000.0)[0] is False
    assert limiter.check("1.2.3.4", now=1061.0)[0] is True


def test_clients_are_limited_independently():
    """One person fat-fingering the password must not lock out the lab."""
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for _ in range(3):
        limiter.record_failure("1.2.3.4", now=1000.0)

    assert limiter.check("1.2.3.4", now=1000.0)[0] is False
    assert limiter.check("5.6.7.8", now=1000.0)[0] is True


def test_a_successful_login_clears_the_count():
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    limiter.record_failure("1.2.3.4", now=1000.0)
    limiter.record_failure("1.2.3.4", now=1000.0)
    limiter.reset("1.2.3.4")

    for _ in range(2):
        assert limiter.check("1.2.3.4", now=1000.0)[0] is True
        limiter.record_failure("1.2.3.4", now=1000.0)


def test_checking_does_not_itself_count_as_an_attempt():
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for _ in range(10):
        assert limiter.check("1.2.3.4", now=1000.0)[0] is True


def test_old_entries_do_not_accumulate():
    """The limiter must not grow without bound on a long-running server."""
    limiter = web_auth.LoginLimiter(max_attempts=3, window_seconds=60)
    for i in range(50):
        limiter.record_failure(f"client-{i}", now=1000.0)
    for i in range(50):
        limiter.check(f"client-{i}", now=2000.0)

    assert limiter._attempts == {}

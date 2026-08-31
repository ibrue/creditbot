"""Session handling for the web client.

The web client authenticates with one shared lab password. That is a weak
secret by nature — it gets screenshotted, pasted into group chats, and
outlives the people who knew it — so everything here is built to limit the
damage rather than to pretend the secret is strong:

- Sessions are signed, not stored: a tampered or expired cookie is simply
  rejected, and there is no server-side session table to grow or leak.
- The signing secret is generated once and kept out of the repo, so
  cookies from one lab cannot be forged with another's.
- Login attempts are rate limited per client, so the password cannot be
  guessed at machine speed.

Deliberately free of FastAPI imports so it can be tested directly.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

SESSION_COOKIE = "creditbot_session"
SESSION_HOURS = 12

# Login rate limiting (per client address)
MAX_ATTEMPTS = 8
WINDOW_SECONDS = 900  # 15 minutes


# ----------------------------------------------------------------- secret

def load_secret(secret_path: str) -> bytes:
    """The key that signs session cookies.

    WEB_SECRET wins if set. Otherwise one is generated and kept beside the
    database, so sessions survive a restart without anyone managing a key.
    """
    from_env = os.getenv("WEB_SECRET", "")
    if from_env:
        return from_env.encode("utf-8")

    try:
        with open(secret_path, "r", encoding="utf-8") as f:
            stored = f.read().strip()
        if stored:
            return stored.encode("utf-8")
    except OSError:
        pass

    generated = secrets.token_urlsafe(48)
    try:
        parent = os.path.dirname(secret_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(secret_path, "w", encoding="utf-8") as f:
            f.write(generated)
        os.chmod(secret_path, 0o600)
    except OSError:
        # Read-only disk or an unwritable path: sessions then last only
        # until the next restart, which is inconvenient but not insecure —
        # and far better than refusing to start.
        pass
    return generated.encode("utf-8")


# ------------------------------------------------------------ passwords

def password_matches(supplied: str, expected: str) -> bool:
    """Constant-time password check. An unset password never matches."""
    if not expected or not supplied:
        return False
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


# --------------------------------------------------------------- tokens

def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def make_token(payload: dict, secret: bytes, ttl_hours: float = SESSION_HOURS,
               now: float | None = None) -> str:
    """Sign a session payload. The result is safe to hand to a browser."""
    body = dict(payload)
    body["exp"] = int((now if now is not None else time.time()) + ttl_hours * 3600)
    encoded = _b64encode(json.dumps(body, separators=(",", ":"),
                                    sort_keys=True).encode("utf-8"))
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def read_token(token: str, secret: bytes, now: float | None = None) -> dict | None:
    """Verify and decode a session token, or None if it is not usable.

    Returns None for anything suspect — bad signature, wrong shape,
    expired, or garbage — so callers only ever handle one failure case.
    """
    if not token or not isinstance(token, str) or token.count(".") != 1:
        return None

    encoded, signature = token.split(".")
    try:
        expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64decode(signature), expected):
            return None
        payload = json.loads(_b64decode(encoded))
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None
    expires = payload.get("exp")
    if not isinstance(expires, (int, float)):
        return None
    if (now if now is not None else time.time()) >= expires:
        return None
    return payload


# --------------------------------------------------------- rate limiting

class LoginLimiter:
    """Sliding-window limit on login attempts, keyed by client address.

    In-process and therefore per-worker, which is the right trade for a lab
    tool: it needs no dependency, and the server runs as a single process.
    """

    # The site is reachable from the internet, so the number of distinct
    # client addresses is attacker-controlled. Keys are swept on a timer and
    # hard-capped, so a spray from many addresses cannot grow this forever.
    MAX_KEYS = 4096

    def __init__(self, max_attempts: int = MAX_ATTEMPTS,
                 window_seconds: int = WINDOW_SECONDS):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}
        self._last_sweep = 0.0

    def _sweep(self, now: float):
        """Drop keys whose attempts have all aged out."""
        cutoff = now - self.window_seconds
        self._attempts = {k: [t for t in v if t > cutoff]
                          for k, v in self._attempts.items()}
        self._attempts = {k: v for k, v in self._attempts.items() if v}
        if len(self._attempts) > self.MAX_KEYS:
            # Still too many: keep the most recent offenders. Dropping a key
            # only forgives attempts, and the survivors are the active ones.
            newest = sorted(self._attempts.items(), key=lambda kv: kv[1][-1],
                            reverse=True)[:self.MAX_KEYS]
            self._attempts = dict(newest)
        self._last_sweep = now

    def _recent(self, key: str, now: float) -> list:
        cutoff = now - self.window_seconds
        recent = [t for t in self._attempts.get(key, []) if t > cutoff]
        if recent:
            self._attempts[key] = recent
        else:
            self._attempts.pop(key, None)
        return recent

    def check(self, key: str, now: float | None = None) -> tuple[bool, int]:
        """(allowed, seconds_until_retry). Does not record an attempt."""
        now = time.time() if now is None else now
        recent = self._recent(key, now)
        if len(recent) < self.max_attempts:
            return True, 0
        retry_after = int(recent[0] + self.window_seconds - now) + 1
        return False, max(retry_after, 1)

    def record_failure(self, key: str, now: float | None = None):
        now = time.time() if now is None else now
        if now - self._last_sweep > self.window_seconds:
            self._sweep(now)
        self._recent(key, now)
        self._attempts.setdefault(key, []).append(now)

    def reset(self, key: str):
        """Called on a successful login so one typo does not linger."""
        self._attempts.pop(key, None)

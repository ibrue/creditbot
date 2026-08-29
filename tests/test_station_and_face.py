"""Tests for the station page (single-identity desktop login) and the
web client's face login."""
import base64

import pytest
from fastapi.testclient import TestClient

import api
import database
import web_auth
import web_face
import webapp

PASSWORD = "lab-password-123"
FRAME = base64.b64encode(b"not-really-a-jpeg").decode("ascii")


@pytest.fixture
def web(db, monkeypatch):
    monkeypatch.setattr(webapp, "WEB_ENABLED", True)
    monkeypatch.setattr(webapp, "WEB_PASSWORD", PASSWORD)
    monkeypatch.setattr(webapp, "WEB_HTTPS", False)
    monkeypatch.setattr(webapp, "_secret_cache", b"test-signing-secret")
    monkeypatch.setattr(webapp, "limiter", web_auth.LoginLimiter())
    with TestClient(api.app) as client:
        yield client


# ------------------------------------------------------------- station

def test_station_page_is_served(web):
    response = web.get("/app/station")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_station_login_binds_the_station_identity(web):
    response = web.post("/app/api/login",
                        json={"password": PASSWORD, "station": True})
    assert response.status_code == 200

    me = web.get("/app/api/me").json()
    assert me["person"] is not None
    assert me["person"]["discord_id"] == webapp.WEB_STATION_ID
    assert me["person"]["name"] == webapp.WEB_STATION_NAME


def test_station_login_creates_the_account_once(web):
    web.post("/app/api/login", json={"password": PASSWORD, "station": True})
    web.post("/app/api/login", json={"password": PASSWORD, "station": True})
    rows = [u for u in database.get_all_users()
            if u["discord_id"] == webapp.WEB_STATION_ID]
    assert len(rows) == 1


def test_station_login_still_needs_the_password(web):
    response = web.post("/app/api/login",
                        json={"password": "wrong", "station": True})
    assert response.status_code == 401
    assert web.get("/app/api/me").status_code == 401


def test_station_can_check_in_immediately(web):
    web.post("/app/api/login", json={"password": PASSWORD, "station": True})
    result = web.post("/app/api/checkin").json()
    assert result["status"] == "checked_in"


def test_plain_login_still_has_no_identity(web):
    web.post("/app/api/login", json={"password": PASSWORD})
    assert web.get("/app/api/me").json()["person"] is None


# ----------------------------------------------------------- face login

class FakeEngine:
    """Stands in for the OpenCV engine: 'sees' whatever the test says."""

    def __init__(self, match=None, sees_face=True):
        self._match = match
        self._sees_face = sees_face

    def detect_best_face(self, frame):
        return "face-row" if self._sees_face else None

    def embed(self, frame, face_row):
        return [0.5] * 8

    def match(self, embedding, known_faces):
        if self._match is None:
            return None, None, 0.1
        return self._match[0], self._match[1], 0.9


@pytest.fixture
def face_engine(monkeypatch):
    """Install a fake engine; tests adjust web_face._engine directly."""
    monkeypatch.setattr(web_face, "_engine", FakeEngine())
    monkeypatch.setattr(web_face, "_engine_error", None)
    monkeypatch.setattr(web_face, "_decode_bgr", lambda raw: "frame")
    return monkeypatch


@pytest.fixture
def signed_in(web):
    assert web.post("/app/api/login",
                    json={"password": PASSWORD}).status_code == 200
    return web


def test_face_endpoints_need_a_session(web):
    """face-login is always session-only.

    face-status is not: the password-free enrol page has to ask whether
    the camera models are loaded before it offers to register anyone. It
    is limited to the lab network instead (see the Funnel tests below).
    """
    web.cookies.clear()
    assert web.post("/app/api/face-login",
                    json={"image_b64": FRAME}).status_code == 401
    assert web.get("/app/api/face-status").status_code == 200
    assert web.get("/app/api/face-status",
                   headers={"Tailscale-Funnel-Request": "?1"}).status_code == 403


def test_face_status_reports_unavailable_without_the_engine(signed_in, monkeypatch):
    monkeypatch.setattr(web_face, "_engine", None)
    monkeypatch.setattr(web_face, "_engine_error", "no cv2 in tests")
    assert signed_in.get("/app/api/face-status").json() == {"available": False}


def test_face_login_503s_without_the_engine(signed_in, monkeypatch):
    monkeypatch.setattr(web_face, "_engine", None)
    monkeypatch.setattr(web_face, "_engine_error", "no cv2 in tests")
    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 503


def test_face_login_signs_the_matched_person_in(signed_in, face_engine):
    database.get_or_create_user("42", "alice")
    database.save_face_encoding("42", "alice", "[0.5]")
    face_engine.setattr(web_face, "_engine", FakeEngine(match=("42", "alice")))

    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 200
    assert response.json()["name"] == "alice"

    me = signed_in.get("/app/api/me").json()
    assert me["person"]["discord_id"] == "42"

    result = signed_in.post("/app/api/checkin").json()
    assert result["status"] == "checked_in"


def test_an_unrecognized_face_is_a_404_not_a_login(signed_in, face_engine):
    database.save_face_encoding("42", "alice", "[0.5]")
    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 404
    assert signed_in.get("/app/api/me").json()["person"] is None


def test_no_face_in_frame_is_a_422(signed_in, face_engine):
    database.save_face_encoding("42", "alice", "[0.5]")
    face_engine.setattr(web_face, "_engine", FakeEngine(sees_face=False))
    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 422


def test_nobody_enrolled_is_a_409(signed_in, face_engine):
    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 409


def test_bad_base64_is_rejected(signed_in, face_engine):
    response = signed_in.post("/app/api/face-login",
                              json={"image_b64": "@@not-base64@@"})
    assert response.status_code == 400


def test_a_corrupt_enrollment_row_does_not_break_login(signed_in, face_engine):
    database.save_face_encoding("13", "mallory", "{corrupt json")
    database.get_or_create_user("42", "alice")
    database.save_face_encoding("42", "alice", "[0.5]")
    face_engine.setattr(web_face, "_engine", FakeEngine(match=("42", "alice")))
    response = signed_in.post("/app/api/face-login", json={"image_b64": FRAME})
    assert response.status_code == 200


# --------------------------------------------------------------- kiosk
# The walk-up terminal at /app/kiosk: the password arms the machine, each
# action identifies whoever is standing there, and the identity is dropped
# afterwards so the next person never inherits the previous one's session.

def test_kiosk_page_is_served(web):
    response = web.get("/app/kiosk")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_kiosk_page_needs_the_web_client_enabled(web, monkeypatch):
    monkeypatch.setattr(webapp, "WEB_ENABLED", False)
    assert web.get("/app/kiosk").status_code == 404


def test_forget_requires_a_session(web):
    assert web.post("/app/api/forget").status_code == 401


def test_forget_drops_the_person_but_keeps_the_session(web, db):
    database.get_or_create_user("42", "alice")
    web.post("/app/api/login", json={"password": PASSWORD})
    web.post("/app/api/select", json={"discord_id": "42"})
    assert web.get("/app/api/me").json()["person"]["name"] == "alice"

    assert web.post("/app/api/forget").status_code == 200

    me = web.get("/app/api/me").json()
    assert me["signed_in"] is True       # still past the password
    assert me["person"] is None          # but nobody is identified


def test_after_forget_actions_need_a_fresh_identity(web, db):
    database.get_or_create_user("42", "alice")
    web.post("/app/api/login", json={"password": PASSWORD})
    web.post("/app/api/select", json={"discord_id": "42"})
    web.post("/app/api/forget")

    # 409 is "pick who you are first" — the next person cannot act as alice.
    assert web.post("/app/api/checkin").status_code == 409


def test_forget_leaves_the_station_identity_recoverable(web, db):
    """Forgetting is per-session state, not a change to the database."""
    database.get_or_create_user("42", "alice")
    web.post("/app/api/login", json={"password": PASSWORD})
    web.post("/app/api/select", json={"discord_id": "42"})
    web.post("/app/api/forget")

    web.post("/app/api/select", json={"discord_id": "42"})
    assert web.get("/app/api/me").json()["person"]["name"] == "alice"
    assert database.get_user("42") is not None


# ------------------------------------------------- self-enrolment + scanning
# /app/enroll takes no password so a newcomer can register at the lab
# terminal, but the server refuses it over Tailscale Funnel so the open
# door does not extend to the public internet.

FUNNEL = {"Tailscale-Funnel-Request": "?1"}


@pytest.fixture
def faces(monkeypatch):
    """A stub face engine: every frame has a face, nothing matches."""
    monkeypatch.setattr(web_face, "available", lambda: True)
    monkeypatch.setattr(web_face, "scan", lambda _b64: {
        "frame": {"w": 640, "h": 480},
        "face": {"x": 10.0, "y": 20.0, "w": 100.0, "h": 120.0},
        "match": None, "detail": "Face not recognized",
    })
    monkeypatch.setattr(web_face, "embed_face", lambda _b64: [0.5] * 128)


def test_enroll_page_is_served(web):
    assert web.get("/app/enroll").status_code == 200


def test_enroll_needs_no_password(web, db, faces):
    response = web.post("/app/api/enroll",
                        json={"name": "alice", "samples": [FRAME, FRAME]})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "alice"
    assert body["samples"] == 2

    # The member exists and is enrolled, so they can be picked and matched.
    assert any(u["username"] == "alice" for u in database.get_all_users())
    assert len(database.get_face_encodings()) == 2


def test_enroll_is_refused_over_funnel(web, db, faces):
    response = web.post("/app/api/enroll",
                        json={"name": "mallory", "samples": [FRAME]},
                        headers=FUNNEL)
    assert response.status_code == 403
    assert not database.get_all_users()


def test_enroll_over_funnel_can_be_opened_deliberately(web, db, faces, monkeypatch):
    monkeypatch.setattr(webapp, "WEB_ENROLL_PUBLIC", True)
    response = web.post("/app/api/enroll",
                        json={"name": "alice", "samples": [FRAME]},
                        headers=FUNNEL)
    assert response.status_code == 200


def test_enroll_rejects_a_duplicate_name(web, db, faces):
    database.get_or_create_user("42", "alice")
    response = web.post("/app/api/enroll",
                        json={"name": "Alice", "samples": [FRAME]})
    assert response.status_code == 409


def test_enroll_rejects_frames_with_no_face(web, db, monkeypatch):
    monkeypatch.setattr(web_face, "available", lambda: True)

    def no_face(_b64):
        raise web_face.FaceLoginError(422, "No face in view")
    monkeypatch.setattr(web_face, "embed_face", no_face)

    response = web.post("/app/api/enroll",
                        json={"name": "ghost", "samples": [FRAME]})
    assert response.status_code == 422
    assert not database.get_all_users()


def test_face_scan_returns_the_box_for_the_overlay(web, db, faces):
    web.post("/app/api/login", json={"password": PASSWORD})
    body = web.post("/app/api/face-scan", json={"image_b64": FRAME}).json()
    assert body["face"] == {"x": 10.0, "y": 20.0, "w": 100.0, "h": 120.0}
    assert body["frame"] == {"w": 640, "h": 480}
    assert body["match"] is None


def test_face_scan_signs_in_on_a_match(web, db, monkeypatch):
    database.get_or_create_user("42", "alice")
    monkeypatch.setattr(web_face, "available", lambda: True)
    monkeypatch.setattr(web_face, "scan", lambda _b64: {
        "frame": {"w": 640, "h": 480},
        "face": {"x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0},
        "match": {"discord_id": "42", "name": "alice", "score": 0.9},
        "detail": None,
    })
    web.post("/app/api/login", json={"password": PASSWORD})
    assert web.post("/app/api/face-scan", json={"image_b64": FRAME}).status_code == 200
    assert web.get("/app/api/me").json()["person"]["name"] == "alice"


def test_face_scan_without_a_session_is_refused_over_funnel(web, db, faces):
    response = web.post("/app/api/face-scan", json={"image_b64": FRAME},
                        headers=FUNNEL)
    assert response.status_code == 403

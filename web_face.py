"""Face login for the browser client.

The multi-user page at /app lets people sign in by looking at their
webcam. The browser sends a JPEG frame; this module runs the same
YuNet + SFace models the kiosk uses and matches against the faces
already enrolled at the kiosk (the face_encodings table).

Heavy pieces (OpenCV, the ONNX models) load lazily and their absence is
non-fatal: available() just returns False and the page falls back to the
name picker.
"""
import base64
import json
import os
import sys
import threading

import database

# The kiosk modules import each other by bare name, the way kiosk.py
# runs them, so their folder goes on the path (same trick as the tests).
KIOSK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kiosk")
if KIOSK_DIR not in sys.path:
    sys.path.insert(0, KIOSK_DIR)


class FaceLoginError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


_lock = threading.Lock()
_engine = None
_engine_error: str | None = None


def _get_engine():
    global _engine, _engine_error
    if _engine is None and _engine_error is None:
        try:
            from face_engine import FaceEngine
            _engine = FaceEngine()
        except Exception as e:  # missing cv2, missing models, bad install
            _engine_error = str(e)
            print(f"ℹ️ Face login unavailable: {e}")
    return _engine


def _decode_bgr(raw: bytes):
    """JPEG/PNG bytes -> BGR frame, or None. Separate so tests can fake it."""
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)


def available() -> bool:
    with _lock:
        return _get_engine() is not None


def _decode_or_raise(image_b64: str):
    try:
        raw = base64.b64decode(image_b64, validate=True)
    except Exception:
        raise FaceLoginError(400, "Bad image data")
    frame = _decode_bgr(raw)
    if frame is None:
        raise FaceLoginError(400, "Could not decode the camera frame")
    return frame


def _known_faces() -> list:
    known = []
    for row in database.get_face_encodings():
        try:
            known.append({"discord_id": row["discord_id"],
                          "name": row["name"],
                          "embedding": json.loads(row["embedding"])})
        except (ValueError, KeyError):
            continue  # one corrupt row must not break everyone's login
    return known


def scan(image_b64: str, detect_only: bool = False) -> dict:
    """Locate the face in a frame, and (unless detect_only) recognize it.

    Unlike identify(), the ordinary "nobody there yet" states are results
    rather than exceptions — the kiosk draws a box every frame and needs
    to know where the face is even when it does not yet know whose it is.

    detect_only skips the expensive half (SFace embedding + matching), so
    the box can track at a much higher rate than recognition needs to run.

    Everything the engine already computes is reported, so the kiosk can
    show its workings: the five YuNet landmarks, the detector's own
    confidence, head turn, face size against the size we insist on, and
    the best match score even when nothing clears the threshold.
    """
    from face_engine import COSINE_THRESHOLD, MIN_FACE_SIZE

    with _lock:
        engine = _get_engine()
        if engine is None:
            raise FaceLoginError(
                503, "Face login is not available on this server.")

        frame = _decode_or_raise(image_b64)
        height, width = frame.shape[:2]
        out = {"frame": {"w": int(width), "h": int(height)},
               "face": None, "landmarks": None, "score": None, "yaw": None,
               "too_small": False, "min_size": int(MIN_FACE_SIZE),
               "threshold": float(COSINE_THRESHOLD),
               "match": None, "best_score": None, "detail": None}

        # min_size=0: we want to see a face that is merely too far away,
        # so the client knows where to zoom.
        face = engine.detect_best_face(frame, min_size=0)
        if face is None:
            out["detail"] = "No face in view — center yourself and get closer."
            return out

        x, y, w, h = (float(v) for v in face[:4])
        out["face"] = {"x": max(0.0, x), "y": max(0.0, y),
                       "w": float(w), "h": float(h)}
        out["landmarks"] = [[float(face[4 + i * 2]), float(face[5 + i * 2])]
                            for i in range(5)]
        out["score"] = float(face[14])
        out["yaw"] = float(engine.yaw_ratio(face))
        out["too_small"] = bool(w < MIN_FACE_SIZE or h < MIN_FACE_SIZE)

        if detect_only:
            return out

        if out["too_small"]:
            out["detail"] = "Too far away — come closer."
            return out

        known = _known_faces()
        if not known:
            out["detail"] = "Nobody is enrolled yet — enroll first."
            return out

        embedding = engine.embed(frame, face)
        discord_id, name, score = engine.match(embedding, known)
        out["best_score"] = float(score)
        if discord_id is None:
            out["detail"] = "Face not recognized — hold still, or pick your name."
            return out

        out["match"] = {"discord_id": str(discord_id), "name": str(name),
                        "score": float(score)}
        return out


def embed_face(image_b64: str) -> list:
    """One enrollment sample: the face embedding for this frame.

    Raises FaceLoginError when there is no usable face, so the enrolling
    person gets told why a sample did not count.
    """
    with _lock:
        engine = _get_engine()
        if engine is None:
            raise FaceLoginError(
                503, "Face recognition is not available on this server.")
        frame = _decode_or_raise(image_b64)
        face = engine.detect_best_face(frame)
        if face is None:
            raise FaceLoginError(
                422, "No face in view — center yourself and get closer.")
        return [float(v) for v in engine.embed(frame, face)]


def identify(image_b64: str) -> dict:
    """Recognize the face in a base64 image against enrolled members.

    Returns {discord_id, name, score} or raises FaceLoginError with an
    HTTP status and a message meant to be shown to the person.
    """
    with _lock:
        engine = _get_engine()
        if engine is None:
            raise FaceLoginError(
                503, "Face login is not available on this server.")

        try:
            raw = base64.b64decode(image_b64, validate=True)
        except Exception:
            raise FaceLoginError(400, "Bad image data")
        frame = _decode_bgr(raw)
        if frame is None:
            raise FaceLoginError(400, "Could not decode the camera frame")

        face = engine.detect_best_face(frame)
        if face is None:
            raise FaceLoginError(
                422, "No face in view — center yourself and get closer.")

        embedding = engine.embed(frame, face)

        known = []
        for row in database.get_face_encodings():
            try:
                known.append({"discord_id": row["discord_id"],
                              "name": row["name"],
                              "embedding": json.loads(row["embedding"])})
            except (ValueError, KeyError):
                continue  # one corrupt row must not break everyone's login
        if not known:
            raise FaceLoginError(
                409, "Nobody is enrolled yet — enroll at the kiosk first.")

        discord_id, name, score = engine.match(embedding, known)
        if discord_id is None:
            raise FaceLoginError(
                404, "Face not recognized — try again or pick your name.")
        return {"discord_id": str(discord_id), "name": str(name),
                "score": float(score)}

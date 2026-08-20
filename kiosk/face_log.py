"""Local face capture log.

Every recognized check-in/check-out and every enrollment sample saves the
face crop to disk, organized per person. These captures are training data
for later fine-tuning: run retune_faces.py to rebuild a person's stored
embeddings from their best/most diverse captures.

Only faces of enrolled (opted-in) members are ever saved — unrecognized
faces are never written to disk.

Layout:
  face_log/<discord_id>_<name>/<YYYYmmdd_HHMMSS_ffffff>_<event>[_<score>].jpg

Config (env / kiosk .env):
  KIOSK_FACE_LOG      1 (default) to enable, 0 to disable
  KIOSK_FACE_LOG_DIR  where to store captures (default: kiosk/face_log)
"""
import os
import re
from datetime import datetime

import cv2

ENABLED = os.getenv("KIOSK_FACE_LOG", "1") == "1"
# ~ and %USERPROFILE%-style variables are expanded, so a Documents folder
# like %USERPROFILE%\Documents\CreditBot\faces works in .env
LOG_DIR = os.path.expanduser(os.path.expandvars(os.getenv(
    "KIOSK_FACE_LOG_DIR",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "face_log"),
)))

# Oldest captures are pruned beyond this many files per person
MAX_FILES_PER_PERSON = 500


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "member"


def person_dir(discord_id: str, name: str) -> str:
    return os.path.join(LOG_DIR, f"{discord_id}_{_safe_name(name)}")


def save_capture(discord_id: str, name: str, face_bgr, event: str,
                 score: float | None = None) -> str | None:
    """Save one face crop for an enrolled member. Returns the path or None."""
    if not ENABLED or face_bgr is None or face_bgr.size == 0:
        return None

    directory = person_dir(discord_id, name)
    os.makedirs(directory, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = f"_{score:.2f}" if score is not None else ""
    path = os.path.join(directory, f"{stamp}_{event}{suffix}.jpg")

    try:
        cv2.imwrite(path, face_bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
    except Exception as e:
        print(f"⚠️ Could not save face capture: {e}")
        return None

    _prune(directory)
    return path


def _prune(directory: str):
    """Delete the oldest captures beyond the per-person cap."""
    try:
        files = [os.path.join(directory, f) for f in os.listdir(directory)
                 if f.lower().endswith(".jpg")]
        if len(files) <= MAX_FILES_PER_PERSON:
            return
        files.sort(key=os.path.getmtime)
        for path in files[:len(files) - MAX_FILES_PER_PERSON]:
            os.remove(path)
    except Exception:
        pass


def list_people() -> list[tuple[str, str, str]]:
    """List logged people as (discord_id, name, directory) tuples."""
    people = []
    if not os.path.isdir(LOG_DIR):
        return people
    for entry in sorted(os.listdir(LOG_DIR)):
        directory = os.path.join(LOG_DIR, entry)
        if not os.path.isdir(directory) or "_" not in entry:
            continue
        discord_id, name = entry.split("_", 1)
        people.append((discord_id, name, directory))
    return people

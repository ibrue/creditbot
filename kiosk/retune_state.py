"""Bookkeeping for automatic face retuning.

Retuning rebuilds a person's stored embeddings from their best recent
captures. It is worth doing once someone has accumulated meaningfully
more captures than they had last time — not on every check-in, which
would burn CPU re-deriving the same samples.

This module holds only that decision, and deliberately imports nothing
from OpenCV or numpy, so the scheduling can be tested (and reasoned
about) without the vision stack installed.

State lives beside the captures in <face_log>/.retune_state.json, so it
travels with them if the log directory is moved to Documents.
"""
import json
import os
from datetime import datetime

STATE_FILENAME = ".retune_state.json"

# A first retune needs enough captures to beat the enrollment samples.
MIN_CAPTURES_FOR_FIRST_RETUNE = 12
# After that, retune once this many new captures have accumulated.
MIN_NEW_CAPTURES = 25


def state_path(log_dir: str) -> str:
    return os.path.join(log_dir, STATE_FILENAME)


def load_state(log_dir: str) -> dict:
    """Read the retune state. A missing or damaged file reads as empty —
    the worst case is one unnecessary retune, never a crash at startup."""
    try:
        with open(state_path(log_dir), "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(log_dir: str, state: dict) -> bool:
    """Persist the retune state. Returns False if it could not be written."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(state_path(log_dir), "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
        return True
    except OSError:
        return False


def count_captures(directory: str) -> int:
    """How many capture images this person has on disk."""
    try:
        return sum(1 for f in os.listdir(directory) if f.lower().endswith(".jpg"))
    except OSError:
        return 0


def is_due(discord_id: str, capture_count: int, state: dict) -> bool:
    """Has this person accumulated enough new captures to be worth retuning?"""
    entry = state.get(str(discord_id))
    previous = entry.get("captures") if isinstance(entry, dict) else None

    # An entry we cannot read tells us nothing about a previous retune, so
    # treat it exactly like a person who has never been retuned — the same
    # way a damaged state file reads as empty. (bool is an int subclass in
    # Python, hence the explicit exclusion.)
    if isinstance(previous, bool) or not isinstance(previous, int) or previous < 0:
        return capture_count >= MIN_CAPTURES_FOR_FIRST_RETUNE

    # Captures are pruned per person, so the count can fall. A drop reads as
    # "no new material" rather than a negative difference that would make
    # someone permanently ineligible.
    return (capture_count - previous) >= MIN_NEW_CAPTURES


def due_people(people, state: dict) -> list:
    """Filter (discord_id, name, directory) tuples down to those due."""
    return [
        (discord_id, name, directory)
        for discord_id, name, directory in people
        if is_due(discord_id, count_captures(directory), state)
    ]


def record_retune(state: dict, discord_id: str, capture_count: int,
                  when: datetime | None = None) -> dict:
    """Note that this person has just been retuned. Returns the state."""
    state[str(discord_id)] = {
        "captures": int(capture_count),
        "last_retune": (when or datetime.now()).isoformat(timespec="seconds"),
    }
    return state


def describe(results: list) -> str:
    """One short line summarizing a retune run, for the kiosk status area."""
    retuned = [r for r in results if r.get("status") == "retuned"]
    skipped = [r for r in results if r.get("status") == "skipped"]
    failed = [r for r in results if r.get("status") == "failed"]

    if not results:
        return "Recognition already up to date"
    parts = []
    if retuned:
        names = ", ".join(r["name"] for r in retuned[:3])
        more = f" +{len(retuned) - 3} more" if len(retuned) > 3 else ""
        parts.append(f"Improved {names}{more}")
    if skipped:
        parts.append(f"{len(skipped)} skipped")
    if failed:
        parts.append(f"{len(failed)} failed")
    return " · ".join(parts) or "Recognition already up to date"

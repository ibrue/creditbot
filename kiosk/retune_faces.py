"""Fine-tune face recognition from the local capture log.

Rebuilds each person's stored embeddings on the server from their logged
face captures (face_log/): it re-detects faces in the most recent
captures, computes fresh embeddings, picks a diverse subset (different
angles/lighting beat near-duplicates), and replaces the person's samples
on the server. More varied samples = better recognition.

The kiosk runs this automatically in the background as captures pile up,
and the "Improve Recognition" button runs it on demand — so normally you
never need this script. It stays for manual runs and troubleshooting:

  python retune_faces.py                # retune everyone who is due
  python retune_faces.py --all          # retune everyone, due or not
  python retune_faces.py --person 1234  # retune one Discord ID
  python retune_faces.py --dry-run      # show what would happen

Reads KIOSK_API_URL / KIOSK_API_KEY from the environment or .env, same
as the kiosk itself.
"""
import argparse
import os

import cv2
import numpy as np

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
except ImportError:
    pass

import face_log
import retune_state
from api_client import ApiClient
from face_engine import FaceEngine

MAX_SAMPLES = 15      # embeddings stored per person after retuning
CANDIDATES = 80       # most recent captures considered per person
MIN_USABLE = 3        # fewer usable captures than this and we skip


def load_embeddings(engine: FaceEngine, directory: str):
    """Detect + embed faces in a person's captures, newest first."""
    files = [os.path.join(directory, f) for f in os.listdir(directory)
             if f.lower().endswith(".jpg")]
    files.sort(key=os.path.getmtime, reverse=True)

    embeddings = []
    for path in files[:CANDIDATES]:
        img = cv2.imread(path)
        if img is None:
            continue
        # Upscale small crops so the detector's minimum face size is met
        h, w = img.shape[:2]
        if min(h, w) < 250:
            scale = 250 / min(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        face = engine.detect_best_face(img)
        if face is None:
            continue
        embeddings.append(engine.embed(img, face))
    return embeddings


def pick_diverse(embeddings, k: int):
    """Greedy farthest-point selection: keep the k most varied samples."""
    if len(embeddings) <= k:
        return embeddings
    vectors = np.stack(embeddings)
    chosen = [0]  # most recent capture is always kept
    while len(chosen) < k:
        sims = vectors @ vectors[chosen].T          # cosine (all normalized)
        max_sim_to_chosen = sims.max(axis=1)
        max_sim_to_chosen[chosen] = np.inf
        chosen.append(int(np.argmin(max_sim_to_chosen)))
    return [embeddings[i] for i in chosen]


def retune_person(engine, api, discord_id: str, name: str, directory: str,
                  dry_run: bool = False) -> dict:
    """Retune one person. Never raises — the outcome is in the result dict.

    Returns {"discord_id", "name", "status", "samples", "usable", "detail"}
    where status is "retuned", "skipped" or "failed".
    """
    result = {"discord_id": discord_id, "name": name, "status": "failed",
              "samples": 0, "usable": 0, "detail": ""}
    try:
        embeddings = load_embeddings(engine, directory)
        result["usable"] = len(embeddings)

        if len(embeddings) < MIN_USABLE:
            result["status"] = "skipped"
            result["detail"] = (f"only {len(embeddings)} usable captures "
                                f"(need {MIN_USABLE})")
            return result

        selected = pick_diverse(embeddings, MAX_SAMPLES)
        result["samples"] = len(selected)

        if dry_run:
            result["status"] = "skipped"
            result["detail"] = (f"would replace samples with {len(selected)} "
                                f"of {len(embeddings)} usable captures")
            return result

        # Replace old samples with the new diverse set
        api.delete_faces(discord_id)
        for embedding in selected:
            api.enroll_face(discord_id, name, embedding)

        result["status"] = "retuned"
        result["detail"] = (f"{len(selected)} samples from "
                            f"{len(embeddings)} usable captures")
        return result
    except Exception as e:
        result["detail"] = f"{type(e).__name__}: {e}"
        return result


def retune(api, engine=None, person: str | None = None, dry_run: bool = False,
           only_due: bool = True, progress=None) -> list:
    """Retune people from the capture log and return a result per person.

    only_due limits the run to people who have accumulated enough new
    captures since their last retune (this is what the kiosk schedules);
    pass False to retune everyone regardless.
    """
    engine = engine or FaceEngine()
    log_dir = face_log.LOG_DIR
    state = retune_state.load_state(log_dir)

    people = face_log.list_people()
    if person:
        people = [p for p in people if p[0] == str(person)]
    elif only_due:
        people = retune_state.due_people(people, state)

    results = []
    for discord_id, name, directory in people:
        if progress:
            progress(name)
        outcome = retune_person(engine, api, discord_id, name, directory,
                                dry_run=dry_run)
        results.append(outcome)

        if outcome["status"] == "retuned":
            retune_state.record_retune(
                state, discord_id, retune_state.count_captures(directory))

    if results and not dry_run:
        retune_state.save_state(log_dir, state)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--person", help="only retune this Discord ID")
    parser.add_argument("--all", action="store_true",
                        help="retune everyone, not just those due")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute but don't change anything on the server")
    args = parser.parse_args()

    api_key = os.getenv("KIOSK_API_KEY", "")
    if not api_key:
        raise SystemExit("KIOSK_API_KEY is not set (see .env.example)")
    api = ApiClient(os.getenv("KIOSK_API_URL", "http://localhost:8765"), api_key)

    if not face_log.list_people():
        raise SystemExit(
            f"No captures found in {face_log.LOG_DIR} — use the kiosk a bit first."
        )

    results = retune(api, person=args.person, dry_run=args.dry_run,
                     only_due=not (args.all or args.person))

    if not results:
        print("Nobody is due for retuning yet — captures are still "
              "accumulating. Use --all to retune anyway.")
        return

    icons = {"retuned": "✅", "skipped": "⏭️ ", "failed": "❌"}
    for outcome in results:
        print(f"{icons.get(outcome['status'], '  ')} {outcome['name']} "
              f"({outcome['discord_id']}): {outcome['detail']}")

    if not args.dry_run and any(r["status"] == "retuned" for r in results):
        print("\nDone! The kiosk picks up new faces on its next refresh "
              "(or press 🔄 Refresh Faces).")


if __name__ == "__main__":
    main()

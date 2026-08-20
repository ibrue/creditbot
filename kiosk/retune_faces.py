"""Fine-tune face recognition from the local capture log.

Rebuilds each person's stored embeddings on the server from their logged
face captures (face_log/): it re-detects faces in the most recent
captures, computes fresh embeddings, picks a diverse subset (different
angles/lighting beat near-duplicates), and replaces the person's samples
on the server. More varied samples = better recognition.

Usage:
  python retune_faces.py                # retune everyone in face_log/
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
from api_client import ApiClient
from face_engine import FaceEngine

MAX_SAMPLES = 15      # embeddings stored per person after retuning
CANDIDATES = 80       # most recent captures considered per person


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


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--person", help="only retune this Discord ID")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute but don't change anything on the server")
    args = parser.parse_args()

    api_url = os.getenv("KIOSK_API_URL", "http://localhost:8765")
    api_key = os.getenv("KIOSK_API_KEY", "")
    if not api_key:
        raise SystemExit("KIOSK_API_KEY is not set (see .env.example)")

    api = ApiClient(api_url, api_key)
    engine = FaceEngine()

    people = face_log.list_people()
    if args.person:
        people = [p for p in people if p[0] == args.person]
    if not people:
        raise SystemExit(
            f"No captures found in {face_log.LOG_DIR}"
            + (f" for {args.person}" if args.person else "")
            + " — use the kiosk a bit first."
        )

    for discord_id, name, directory in people:
        embeddings = load_embeddings(engine, directory)
        if len(embeddings) < 3:
            print(f"⏭️  {name} ({discord_id}): only {len(embeddings)} usable "
                  f"captures, skipping (need at least 3)")
            continue

        selected = pick_diverse(embeddings, MAX_SAMPLES)
        if args.dry_run:
            print(f"🔍 {name} ({discord_id}): would replace server samples with "
                  f"{len(selected)} of {len(embeddings)} usable captures")
            continue

        # Replace old samples with the new diverse set
        api.session.delete(f"{api.base_url}/faces/{discord_id}",
                           timeout=api.timeout).raise_for_status()
        for emb in selected:
            api.enroll_face(discord_id, name, emb)
        print(f"✅ {name} ({discord_id}): retuned with {len(selected)} samples "
              f"(from {len(embeddings)} usable captures)")

    if not args.dry_run:
        print("\nDone! The kiosk picks up new faces on its next refresh "
              "(or press 🔄 Refresh Faces).")


if __name__ == "__main__":
    main()

"""Fun, school-friendly captions for kiosk check-in photos via a local LLM.

Uses Ollama (https://ollama.com) with a vision model running on the same
machine — nothing leaves the computer. If Ollama isn't installed or the
request fails, callers get None and the photo posts without a caption.
"""
import base64
import re

import requests

import config

PROMPT = (
    "This is a lighthearted photo of a robotics team member checking in at "
    "the lab kiosk. Write exactly ONE short, playful, school-appropriate "
    "caption for it (under 25 words) — like a fun yearbook caption or a "
    "wholesome meme. You may riff on their outfit, pose, expression, or "
    "energy. STRICT RULES: be kind and family-friendly; no insults or "
    "mockery; never comment on body shape, weight, skin, race, gender, age, "
    "or attractiveness; no innuendo, profanity, or dark humor. "
    "Reply with only the caption text."
)

MAX_CAPTION_LENGTH = 200

# Belt-and-braces filter on top of the prompt rules: if a small local model
# ignores instructions and produces something off-limits, drop the caption
# entirely rather than posting it.
BANNED_WORDS = re.compile(
    r"\b(fat|skinny|ugly|weight|obese|chubby|anorexi\w*|sexy|hot|attractive|"
    r"race|racist|skin\s*color|gender|damn|hell|stupid|idiot|dumb|loser|"
    r"kill|die|death|hate)\b",
    re.IGNORECASE,
)


def generate_caption(photo_path: str) -> str | None:
    """Generate a caption for a photo. Returns None if unavailable/unsafe."""
    if not config.OLLAMA_ENABLED:
        return None

    try:
        with open(photo_path, "rb") as f:
            image_b64 = base64.b64encode(f.read()).decode("ascii")

        response = requests.post(
            f"{config.OLLAMA_URL.rstrip('/')}/api/generate",
            json={
                "model": config.OLLAMA_MODEL,
                "prompt": PROMPT,
                "images": [image_b64],
                "stream": False,
                "options": {"temperature": 0.9},
            },
            timeout=90,
        )
        if response.status_code == 404:
            print(f"ℹ️ No caption: model '{config.OLLAMA_MODEL}' is not pulled — "
                  f"run setup_ollama.bat (or: ollama pull {config.OLLAMA_MODEL})")
            return None
        response.raise_for_status()
        text = response.json().get("response", "")
    except Exception as e:
        print(f"ℹ️ No caption (Ollama unavailable or failed): {e}")
        return None

    caption = " ".join(text.split()).strip().strip('"“”').strip()
    if not caption:
        return None
    if len(caption) > MAX_CAPTION_LENGTH:
        caption = caption[:MAX_CAPTION_LENGTH].rsplit(" ", 1)[0] + "…"
    if BANNED_WORDS.search(caption):
        print(f"ℹ️ Caption dropped by safety filter: {caption!r}")
        return None
    return caption

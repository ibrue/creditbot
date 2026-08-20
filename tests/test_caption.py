"""Tests for the local-LLM photo captions.

Ollama is replaced by a stub so the suite never needs a model or a network.
"""
import pytest
import requests

import caption
import config


class _Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")

    def json(self):
        return self._payload


@pytest.fixture
def photo(tmp_path):
    path = tmp_path / "checkin.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg\xff\xd9")
    return str(path)


@pytest.fixture
def ollama(monkeypatch):
    """Stub Ollama; set .reply / .status / .error to steer it."""
    class Stub:
        status = 200
        reply = "Robot wrangler reporting for duty!"
        error = None
        calls = []

        def post(self, url, json=None, timeout=None):
            self.calls.append({"url": url, "json": json, "timeout": timeout})
            if self.error:
                raise self.error
            return _Response(self.status, {"response": self.reply})

    stub = Stub()
    monkeypatch.setattr(config, "OLLAMA_ENABLED", True)
    monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:11434")
    monkeypatch.setattr(config, "OLLAMA_MODEL", "llava")
    monkeypatch.setattr(caption.requests, "post", stub.post)
    return stub


def test_caption_is_returned(ollama, photo):
    assert caption.generate_caption(photo) == "Robot wrangler reporting for duty!"


def test_request_shape(ollama, photo):
    caption.generate_caption(photo)
    call = ollama.calls[0]

    assert call["url"] == "http://localhost:11434/api/generate"
    assert call["json"]["model"] == "llava"
    assert call["json"]["stream"] is False
    assert len(call["json"]["images"]) == 1
    assert call["timeout"] == 90


def test_trailing_slash_in_url_does_not_double_up(ollama, photo, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_URL", "http://localhost:11434/")
    caption.generate_caption(photo)
    assert ollama.calls[0]["url"] == "http://localhost:11434/api/generate"


def test_quotes_and_whitespace_are_stripped(ollama, photo):
    ollama.reply = '"  Quoted   and   spaced  "'
    assert caption.generate_caption(photo) == "Quoted and spaced"


def test_smart_quotes_are_stripped(ollama, photo):
    ollama.reply = "“Curly quoted”"
    assert caption.generate_caption(photo) == "Curly quoted"


def test_long_captions_are_truncated(ollama, photo):
    ollama.reply = "word " * 100
    result = caption.generate_caption(photo)

    assert len(result) <= caption.MAX_CAPTION_LENGTH + 1
    assert result.endswith("…")


@pytest.mark.parametrize("reply", [
    "What a stupid pose",
    "This loser again",
    "Looking hot today",
    "Nice weight loss",
    "I hate this photo",
])
def test_unsafe_captions_are_dropped(ollama, photo, reply):
    ollama.reply = reply
    assert caption.generate_caption(photo) is None


def test_safe_caption_with_an_innocent_substring_survives(ollama, photo):
    """The filter is word-bounded, so 'hotdog' must not trip 'hot'."""
    ollama.reply = "Powered by hotdogs and determination"
    assert caption.generate_caption(photo) == "Powered by hotdogs and determination"


@pytest.mark.parametrize("reply", ["", "   "])
def test_empty_captions_become_none(ollama, photo, reply):
    ollama.reply = reply
    assert caption.generate_caption(photo) is None


def test_missing_model_returns_none(ollama, photo):
    ollama.status = 404
    assert caption.generate_caption(photo) is None


def test_server_error_returns_none(ollama, photo):
    ollama.status = 500
    assert caption.generate_caption(photo) is None


def test_ollama_offline_returns_none(ollama, photo):
    ollama.error = requests.ConnectionError("connection refused")
    assert caption.generate_caption(photo) is None


def test_timeout_returns_none(ollama, photo):
    ollama.error = requests.Timeout("timed out")
    assert caption.generate_caption(photo) is None


def test_missing_photo_file_returns_none(ollama, tmp_path):
    assert caption.generate_caption(str(tmp_path / "gone.jpg")) is None


def test_disabled_skips_ollama_entirely(ollama, photo, monkeypatch):
    monkeypatch.setattr(config, "OLLAMA_ENABLED", False)

    assert caption.generate_caption(photo) is None
    assert ollama.calls == []


def test_prompt_carries_the_safety_rules():
    prompt = caption.PROMPT.lower()
    for rule in ["school-appropriate", "kind and family-friendly", "no insults"]:
        assert rule in prompt

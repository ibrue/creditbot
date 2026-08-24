"""In-memory server log for the admin page's terminal panel.

Everything the server prints (kiosk check-ins, web check-ins, warnings)
is mirrored into a small ring buffer that /app/api/admin/logs serves to
the browser. Nothing is written to disk and the buffer is bounded, so
this can run forever on a NAS without growing.
"""
import logging
import sys
import threading
import time
from collections import deque

MAX_LINES = 500

_lines: deque = deque(maxlen=MAX_LINES)
_seq = 0
_lock = threading.Lock()
_installed = False


def record(text: str):
    global _seq
    for part in str(text).splitlines():
        if not part.strip():
            continue
        with _lock:
            _seq += 1
            _lines.append({"n": _seq, "t": time.strftime("%H:%M:%S"),
                           "line": part})


def since(n: int) -> list:
    """Lines newer than sequence number n, oldest first."""
    with _lock:
        return [dict(item) for item in _lines if item["n"] > n]


class _Tee:
    """Wraps a real stream; everything written also lands in the buffer."""

    def __init__(self, stream):
        self._stream = stream

    def write(self, text):
        result = self._stream.write(text)
        try:
            record(text)
        except Exception:
            pass  # the terminal panel must never break real output
        return result

    def flush(self):
        self._stream.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)


class _BufferHandler(logging.Handler):
    """Catches log records (uvicorn access/error lines) into the buffer."""

    def emit(self, log_record):
        try:
            record(self.format(log_record))
        except Exception:
            pass


def install():
    """Start capturing stdout/stderr and log records. Idempotent."""
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _Tee(sys.stdout)
    sys.stderr = _Tee(sys.stderr)

    handler = _BufferHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
    logging.getLogger().addHandler(handler)
    # uvicorn's loggers don't propagate to root, so hook them directly
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        if not logger.propagate:
            logger.addHandler(handler)

    record("— terminal capture started —")

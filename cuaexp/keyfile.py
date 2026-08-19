"""Load the OpenAI key from an external file.

The key stays where it is on disk. It is never written into this project, never
logged, and never echoed. Only the *path* is configured (see config.KEYFILE).
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import KEYFILE

_CACHED: str | None = None
_KEY_NAMES = ("OPENAI_API_KEY", "OPENAI_KEY", "API_KEY", "KEY")


def _parse(text: str) -> str | None:
    # Form 1: dotenv-style, KEY=value (possibly quoted, possibly `export KEY=`).
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^export\s+", "", line)
        if "=" in line:
            name, _, value = line.partition("=")
            name = name.strip().upper()
            value = value.strip().strip('"').strip("'")
            if value and any(k in name for k in _KEY_NAMES):
                return value
    # Form 2: the file is just the bare key.
    for raw in text.splitlines():
        line = raw.strip().strip('"').strip("'")
        if line and not line.startswith("#") and "=" not in line and len(line) > 20:
            return line
    return None


def load_key(path: Path | None = None) -> str:
    global _CACHED
    if _CACHED:
        return _CACHED
    p = Path(path) if path else KEYFILE
    if not p.exists():
        raise RuntimeError(f"Key file not found at {p} (set CUAEXP_KEYFILE)")
    key = _parse(p.read_text(encoding="utf-8", errors="replace"))
    if not key:
        raise RuntimeError(f"Could not find an API key inside {p}")
    _CACHED = key
    return key


def redact(text: str) -> str:
    """Strip anything key-shaped out of a string before it is logged."""
    return re.sub(r"\b(sk-[A-Za-z0-9_\-]{8,})", "sk-***REDACTED***", text)

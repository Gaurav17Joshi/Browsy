"""Small persistent note store the agent can write to and read back.

Survives across runs and across recycle (↻), unlike the conversation, so it is
where anything worth keeping goes: a working URL, a site's quirks, a user
preference. Kept as flat JSON -- it is meant to be readable and hand-editable.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import PROJECT_ROOT

STORE = PROJECT_ROOT / "memory.json"
MAX_NOTES = 300


def _load() -> list[dict]:
    if not STORE.exists():
        return []
    try:
        data = json.loads(STORE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save(notes: list[dict]) -> None:
    STORE.write_text(json.dumps(notes[-MAX_NOTES:], indent=2, ensure_ascii=False),
                     encoding="utf-8")


def remember(note: str, tag: str = "") -> str:
    notes = _load()
    entry = {"t": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "tag": tag.strip().lower(), "note": note.strip()}
    # replace an existing note with the same tag rather than piling up duplicates
    if entry["tag"]:
        notes = [n for n in notes if n.get("tag") != entry["tag"]]
    notes.append(entry)
    _save(notes)
    return f'remembered{f" [{entry["tag"]}]" if entry["tag"] else ""}: {entry["note"][:120]}'


def recall(query: str = "") -> str:
    notes = _load()
    if not notes:
        return "(nothing remembered yet)"
    q = query.strip().lower()
    if q:
        terms = [t for t in q.split() if t]
        notes = [n for n in notes
                 if any(t in n.get("note", "").lower() or t in n.get("tag", "")
                        for t in terms)]
        if not notes:
            return f"(no notes matching {query!r})"
    return "\n".join(f'- [{n.get("tag") or "note"}] {n["note"]}' for n in notes[-40:])


def forget(tag: str) -> str:
    notes = _load()
    before = len(notes)
    notes = [n for n in notes if n.get("tag") != tag.strip().lower()]
    _save(notes)
    return f"forgot {before - len(notes)} note(s) tagged {tag!r}"


def preamble() -> str:
    """Injected into the system prompt so notes are available without a tool call."""
    notes = _load()
    if not notes:
        return ""
    lines = "\n".join(f'- [{n.get("tag") or "note"}] {n["note"]}' for n in notes[-25:])
    return f"\n\nTHINGS YOU REMEMBER FROM EARLIER SESSIONS:\n{lines}\n"

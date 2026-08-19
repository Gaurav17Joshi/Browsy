"""A single searchable record of every question ever asked.

The per-run folders under logs/ hold the full detail, but they are one directory
per run and cost is aggregated per run rather than per question. This is the
cross-run view: one line per query -> response, with the tokens, context size and
cost that question actually consumed, and a pointer back to its run folder.

Two files, both regenerated safely:
    history/queries.jsonl   append-only, one JSON object per query
    history/index.md        human-readable table, newest first
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import PROJECT_ROOT

HISTORY_DIR = PROJECT_ROOT / "history"
QUERIES = HISTORY_DIR / "queries.jsonl"
INDEX = HISTORY_DIR / "index.md"
INDEX_ROWS = 200


def append(record: dict[str, Any]) -> None:
    HISTORY_DIR.mkdir(exist_ok=True)
    with QUERIES.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    try:
        write_index()
    except Exception:
        pass          # the index is a convenience; never let it break a run


def load() -> list[dict]:
    if not QUERIES.exists():
        return []
    out = []
    for line in QUERIES.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def write_index() -> Path:
    rows = load()
    HISTORY_DIR.mkdir(exist_ok=True)

    tot_cost = sum(r.get("cost_usd", 0) for r in rows)
    tot_in = sum(r.get("input_tokens", 0) for r in rows)
    tot_cached = sum(r.get("cached_input_tokens", 0) for r in rows)
    tot_out = sum(r.get("output_tokens", 0) for r in rows)

    by_day: dict[str, dict] = {}
    for r in rows:
        day = (r.get("when") or "")[:10]
        d = by_day.setdefault(day, {"n": 0, "cost": 0.0, "secs": 0.0})
        d["n"] += 1
        d["cost"] += r.get("cost_usd", 0)
        d["secs"] += r.get("seconds", 0)

    lines = [
        "# Query history", "",
        f"**{len(rows)} queries · ${tot_cost:.4f} total · "
        f"{tot_in:,} in ({tot_cached / max(tot_in, 1):.0%} cached) / {tot_out:,} out**", "",
        "## By day", "", "| day | queries | cost | time |", "|---|---:|---:|---:|",
    ]
    for day in sorted(by_day, reverse=True):
        d = by_day[day]
        lines.append(f"| {day} | {d['n']} | ${d['cost']:.4f} | {d['secs']:.0f}s |")

    lines += ["", f"## Queries (newest first, last {INDEX_ROWS})", "",
              "| when | query | answer | tools | ctx | tok in (cached) | out | cost | time | run |",
              "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for r in reversed(rows[-INDEX_ROWS:]):
        cached = r.get("cached_input_tokens", 0)
        tin = r.get("input_tokens", 0)
        pct = f"{cached / tin:.0%}" if tin else "-"
        lines.append(
            f"| {(r.get('when') or '')[:16].replace('T', ' ')} "
            f"| {_clip(r.get('query', ''), 70)} "
            f"| {_clip(r.get('response', ''), 70)} "
            f"| {r.get('tool_calls', 0)} "
            f"| {r.get('max_context_tokens', 0):,} "
            f"| {tin:,} ({pct}) "
            f"| {r.get('output_tokens', 0):,} "
            f"| ${r.get('cost_usd', 0):.4f} "
            f"| {r.get('seconds', 0):.1f}s "
            f"| `{r.get('run_id', '')}` |")

    INDEX.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return INDEX


def search(term: str) -> Iterator[dict]:
    t = term.lower()
    for r in load():
        if t in (r.get("query", "") + " " + r.get("response", "")).lower():
            yield r


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

"""Run logging: every API call, every tool call, tokens and cost.

Writes three files per run under logs/<run-id>/:
  events.jsonl   append-only stream of everything that happened
  summary.json   totals: turns, tools, tokens, cost
  transcript.md  human-readable
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import history
from .config import LOG_DIR, PRICING
from .keyfile import redact


def price(model: str, uncached_in: int, cached_in: int, out: int) -> float:
    rates = PRICING.get(model)
    if not rates:
        base = model.rsplit("-", 1)[0]
        rates = PRICING.get(base, PRICING["gpt-5.6-terra"])
    pin, pcached, pout = rates
    return (uncached_in * pin + cached_in * pcached + out * pout) / 1_000_000


@dataclass
class Totals:
    requests: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float = 0.0
    tool_calls: int = 0
    by_model: dict[str, dict[str, float]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "tool_calls": self.tool_calls,
            "cost_usd": round(self.cost_usd, 6),
            "by_model": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                             for kk, vv in v.items()} for k, v in self.by_model.items()},
        }


class Recorder:
    def __init__(self, name: str, task: str = ""):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:40]
        self.run_id = f"{stamp}-{safe}"
        self.dir = LOG_DIR / self.run_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.events = self.dir / "events.jsonl"
        self.totals = Totals()
        self.t0 = time.time()
        self.task = task
        self.transcript: list[str] = [f"# {name}\n", f"**Task:** {task}\n"]
        self.log("run_start", {"name": name, "task": task,
                               "started": datetime.now(timezone.utc).isoformat()})

    # --- images -------------------------------------------------------------
    def save_image(self, data: bytes | str, label: str = "shot") -> Path:
        """Persist a PNG next to the run log and return its path."""
        import base64 as _b64
        shots = self.dir / "screenshots"
        shots.mkdir(exist_ok=True)
        self._shot_n = getattr(self, "_shot_n", 0) + 1
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label)[:40]
        path = shots / f"{self._shot_n:03d}-{safe}.png"
        raw = _b64.b64decode(data) if isinstance(data, str) else data
        path.write_bytes(raw)
        self.log("screenshot_saved", {"path": str(path.relative_to(self.dir)),
                                      "bytes": len(raw), "label": label})
        self.md(f"  - ![{label}](screenshots/{path.name})")
        return path

    # --- primitives ---------------------------------------------------------
    def log(self, kind: str, data: dict[str, Any]) -> None:
        rec = {"t": round(time.time() - self.t0, 3), "kind": kind, **data}
        with self.events.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    def md(self, line: str) -> None:
        self.transcript.append(line)

    # --- per-turn accounting ------------------------------------------------
    # Run totals answer "what did this session cost". These answer the more
    # useful question: "what did *that question* cost, and how big had the
    # context grown by the time it finished".
    def begin_turn(self, query: str) -> None:
        self._turn = {
            "when": history.stamp(),
            "query": query,
            "t0": time.time(),
            "base": (self.totals.requests, self.totals.input_tokens,
                     self.totals.cached_tokens, self.totals.output_tokens,
                     self.totals.cost_usd, self.totals.tool_calls),
            "tools": [],
            "max_context_tokens": 0,
        }

    def end_turn(self, response: str, url: str = "") -> dict | None:
        t = getattr(self, "_turn", None)
        if not t:
            return None
        self._turn = None
        r0, i0, c0, o0, cost0, tc0 = t["base"]
        counts: dict[str, int] = {}
        for name in t["tools"]:
            counts[name] = counts.get(name, 0) + 1
        rec = {
            "when": t["when"],
            "run_id": self.run_id,
            "query": t["query"],
            "response": response,
            "url": url,
            "seconds": round(time.time() - t["t0"], 1),
            "requests": self.totals.requests - r0,
            "tool_calls": self.totals.tool_calls - tc0,
            "tools": counts,
            "input_tokens": self.totals.input_tokens - i0,
            "cached_input_tokens": self.totals.cached_tokens - c0,
            "output_tokens": self.totals.output_tokens - o0,
            "max_context_tokens": t["max_context_tokens"],
            "cost_usd": round(self.totals.cost_usd - cost0, 6),
            "log_dir": str(self.dir.relative_to(self.dir.parent.parent)),
        }
        self.log("turn", rec)
        try:
            history.append(rec)
        except Exception:
            pass          # history is a convenience; never let it break a run
        return rec

    # --- domain events ------------------------------------------------------
    def user(self, text: str) -> None:
        self.log("user", {"text": text})
        self.md(f"\n---\n\n### User\n{text}\n")

    def assistant(self, text: str) -> None:
        self.log("assistant", {"text": text})
        self.md(f"\n### Assistant\n{text}\n")

    def tool_call(self, name: str, args: Any) -> None:
        self.totals.tool_calls += 1
        if getattr(self, "_turn", None):
            self._turn["tools"].append(name)
        self.log("tool_call", {"tool": name, "args": args})
        a = json.dumps(args, ensure_ascii=False, default=str)
        self.md(f"- **{name}**(`{redact(a)[:300]}`)")

    def tool_result(self, name: str, result: str, ms: float | None = None) -> None:
        self.log("tool_result", {"tool": name, "result": result[:4000], "ms": ms})
        snippet = redact(result.replace("\n", " "))[:200]
        self.md(f"  - -> {snippet}")

    def api_usage(self, model: str, usage: Any) -> None:
        """Record one model request's token usage and cost."""
        try:
            total_in = int(getattr(usage, "input_tokens", 0) or 0)
            out = int(getattr(usage, "output_tokens", 0) or 0)
            det_in = getattr(usage, "input_tokens_details", None)
            cached = int(getattr(det_in, "cached_tokens", 0) or 0) if det_in else 0
            det_out = getattr(usage, "output_tokens_details", None)
            reasoning = int(getattr(det_out, "reasoning_tokens", 0) or 0) if det_out else 0
            reqs = int(getattr(usage, "requests", 1) or 1)
        except Exception:
            return
        cached = min(cached, total_in)
        cost = price(model, total_in - cached, cached, out)

        # The largest single request in a turn is how big the context actually
        # grew -- summing across requests would just count the same prefix again.
        if getattr(self, "_turn", None):
            self._turn["max_context_tokens"] = max(
                self._turn["max_context_tokens"], total_in)

        self.totals.requests += reqs
        self.totals.input_tokens += total_in
        self.totals.cached_tokens += cached
        self.totals.output_tokens += out
        self.totals.reasoning_tokens += reasoning
        self.totals.cost_usd += cost
        m = self.totals.by_model.setdefault(
            model, {"requests": 0, "input_tokens": 0, "cached_input_tokens": 0,
                    "output_tokens": 0, "cost_usd": 0.0})
        m["requests"] += reqs
        m["input_tokens"] += total_in
        m["cached_input_tokens"] += cached
        m["output_tokens"] += out
        m["cost_usd"] += cost

        self.log("api_usage", {"model": model, "requests": reqs,
                               "input_tokens": total_in, "cached_input_tokens": cached,
                               "output_tokens": out, "reasoning_tokens": reasoning,
                               "cost_usd": round(cost, 6)})

    def error(self, where: str, err: str) -> None:
        self.log("error", {"where": where, "error": redact(err)[:2000]})
        self.md(f"\n> **error in {where}:** {redact(err)[:500]}\n")

    # --- finish -------------------------------------------------------------
    def finish(self, outcome: str = "", ok: bool | None = None) -> dict:
        summary = {
            "run_id": self.run_id,
            "task": self.task,
            "ok": ok,
            "outcome": outcome[:4000],
            "wall_seconds": round(time.time() - self.t0, 1),
            **self.totals.as_dict(),
        }
        self.log("run_end", summary)
        (self.dir / "summary.json").write_text(
            json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
        t = self.totals
        self.md(
            f"\n---\n\n## Summary\n\n"
            f"| metric | value |\n|---|---|\n"
            f"| wall time | {summary['wall_seconds']}s |\n"
            f"| model requests | {t.requests} |\n"
            f"| tool calls | {t.tool_calls} |\n"
            f"| input tokens | {t.input_tokens:,} ({t.cached_tokens:,} cached) |\n"
            f"| output tokens | {t.output_tokens:,} |\n"
            f"| **cost** | **${t.cost_usd:.4f}** |\n"
        )
        (self.dir / "transcript.md").write_text("\n".join(self.transcript), encoding="utf-8")
        return summary

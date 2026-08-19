#!/usr/bin/env python
"""Run the eval suite and write a cost/performance report.

    python evals/run_suite.py            # all tasks
    python evals/run_suite.py 3 7 bonus  # only those ids
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents import ItemHelpers, RunItemStreamEvent   # noqa: E402

from cuaexp.agent import BrowserAgent                # noqa: E402
from cuaexp.recorder import Recorder                 # noqa: E402
from cuaexp.session import BrowserSession            # noqa: E402
from evals.tasks import TASKS                        # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(name)-16s %(message)s",
                    datefmt="%H:%M:%S")
for noisy in ("httpx", "openai", "cuaexp.cdp"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("suite")

OUT = ROOT / "evals" / "results"
OUT.mkdir(parents=True, exist_ok=True)


def printer(counts: dict):
    def on_event(ev):
        if isinstance(ev, RunItemStreamEvent):
            if ev.name == "tool_called":
                raw = getattr(ev.item, "raw_item", None)
                name = getattr(raw, "name", None) or getattr(raw, "type", "?")
                counts[name] = counts.get(name, 0) + 1
                print(f"    -> {name}", flush=True)
            elif ev.name == "message_output_created":
                t = ItemHelpers.text_message_output(ev.item).strip()
                if t:
                    print(f"    {t[:150]}", flush=True)
    return on_event


async def run_one(spec: dict) -> dict:
    name = f"eval{spec['id']}-{spec['name']}"
    rec = Recorder(name, spec["task"])
    sess = BrowserSession(rec, allow=[], trail=False)
    counts: dict = {}
    t0 = time.time()
    final, err = "", None

    print(f"\n{'=' * 70}\n[{spec['id']}] {spec['level']}  {spec['name']}\n{spec['task'][:160]}\n")
    try:
        await sess.start()
        if spec.get("start") and spec["start"] != "about:blank":
            await sess.act.navigate(spec["start"])
        agent = BrowserAgent(sess, rec)
        final = await agent.send(spec["task"], on_event=printer(counts))
    except Exception as e:
        err = f"{type(e).__name__}: {e}"
        log.exception("task %s crashed", spec["id"])
        rec.error("suite", err)
    finally:
        try:
            await sess.close()
        except Exception:
            pass

    def _match(pattern: str) -> bool:
        try:
            return bool(re.search(pattern, final or ""))
        except re.error as e:          # a broken check must not sink the run
            log.warning("bad check pattern %r: %s", pattern, e)
            return False

    hits = [p for p in spec.get("check", []) if _match(p)]
    passed = (not err) and len(hits) == len(spec.get("check", []))
    summary = rec.finish(final, passed)

    row = {
        "id": spec["id"], "level": spec["level"], "name": spec["name"],
        "passed": passed, "error": err,
        "checks_hit": f"{len(hits)}/{len(spec.get('check', []))}",
        "seconds": round(time.time() - t0, 1),
        "requests": summary["requests"], "tool_calls": summary["tool_calls"],
        "input_tokens": summary["input_tokens"],
        "cached_input_tokens": summary["cached_input_tokens"],
        "output_tokens": summary["output_tokens"],
        "cost_usd": summary["cost_usd"],
        "tools": counts, "run_id": summary["run_id"],
        "answer": (final or "")[:1500],
    }
    print(f"\n  {'PASS' if passed else 'FAIL'}  {row['seconds']}s  "
          f"{row['tool_calls']} tools  ${row['cost_usd']:.4f}")
    return row


async def main() -> int:
    wanted = sys.argv[1:]
    specs = [t for t in TASKS if not wanted or str(t["id"]) in wanted]
    rows = []
    for spec in specs:
        rows.append(await run_one(spec))
        (OUT / "results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        await asyncio.sleep(1)

    tot_cost = sum(r["cost_usd"] for r in rows)
    tot_in = sum(r["input_tokens"] for r in rows)
    tot_cached = sum(r["cached_input_tokens"] for r in rows)
    tot_out = sum(r["output_tokens"] for r in rows)
    tot_s = sum(r["seconds"] for r in rows)
    npass = sum(1 for r in rows if r["passed"])

    lines = [
        "# Eval suite results", "",
        f"{npass}/{len(rows)} passed automated checks | "
        f"total ${tot_cost:.4f} | {tot_s:.0f}s wall | "
        f"{tot_in:,} in ({tot_cached / max(tot_in, 1):.0%} cached) / {tot_out:,} out", "",
        "| # | level | task | ok | time | tools | reqs | tok in (cached) | tok out | cost |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['level']} | {r['name']} | "
            f"{'PASS' if r['passed'] else 'FAIL'} | {r['seconds']}s | {r['tool_calls']} | "
            f"{r['requests']} | {r['input_tokens']:,} "
            f"({r['cached_input_tokens'] / max(r['input_tokens'], 1):.0%}) | "
            f"{r['output_tokens']:,} | ${r['cost_usd']:.4f} |")

    tool_totals: dict = {}
    for r in rows:
        for k, v in r["tools"].items():
            tool_totals[k] = tool_totals.get(k, 0) + v
    lines += ["", "## Tool usage", "", "| tool | calls |", "|---|---|"]
    for k, v in sorted(tool_totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {k} | {v} |")

    lines += ["", "## Answers", ""]
    for r in rows:
        lines += [f"### [{r['id']}] {r['name']} — {'PASS' if r['passed'] else 'FAIL'}"
                  f" ({r['checks_hit']} checks)",
                  f"`logs/{r['run_id']}/`", ""]
        if r["error"]:
            lines += [f"**crashed:** `{r['error']}`", ""]
        lines += ["```", (r["answer"] or "(no answer)")[:1200], "```", ""]

    (OUT / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n" + "\n".join(lines[:8]))
    print(f"\nwrote {OUT / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

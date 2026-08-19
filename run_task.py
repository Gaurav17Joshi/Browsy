#!/usr/bin/env python
"""Headless-ish task runner: one task, full logs, printed summary.

    python run_task.py "find X on Y"
    python run_task.py --headless --allow example.com "..."
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from agents import RunItemStreamEvent

from cuaexp.agent import BrowserAgent
from cuaexp.recorder import Recorder
from cuaexp.session import BrowserSession

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)-16s %(message)s",
    datefmt="%H:%M:%S",
)
logging.getLogger("openai").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)


def make_printer():
    def on_event(ev):
        if isinstance(ev, RunItemStreamEvent):
            item = ev.item
            if ev.name == "tool_called":
                raw = getattr(item, "raw_item", None)
                name = getattr(raw, "name", "?")
                args = (getattr(raw, "arguments", "") or "")[:160]
                print(f"  -> {name}({args})", flush=True)
            elif ev.name == "message_output_created":
                from agents import ItemHelpers
                text = ItemHelpers.text_message_output(item).strip()
                if text:
                    print(f"\n{text}\n", flush=True)
    return on_event


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("task", nargs="+")
    ap.add_argument("--name", default="task")
    ap.add_argument("--allow", default="", help="comma-separated allowed domains")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--start", default="", help="URL to open first")
    ap.add_argument("--no-cursor", action="store_true",
                    help="disable the visible virtual mouse pointer")
    ap.add_argument("--shots", action="store_true",
                    help="save a screenshot after every page-changing action")
    args = ap.parse_args()

    # "--then" splits one invocation into several conversation turns, all
    # sharing one context and one browser -- that continuity is the product.
    joined = " ".join(args.task)
    turns = [t.strip() for t in joined.split("--then") if t.strip()]
    task = turns[0]
    allow = [d.strip() for d in args.allow.split(",") if d.strip()]

    rec = Recorder(args.name, " || ".join(turns))
    sess = BrowserSession(rec, allow=allow, headless=args.headless, trail=args.shots,
                          cursor=not args.no_cursor)
    print(f"\n=== {rec.run_id} ===")
    for i, t in enumerate(turns, 1):
        print(f"TURN {i}: {t}")
    print()

    ok = None
    final = ""
    try:
        await sess.start()
        if args.start:
            await sess.act.navigate(args.start)
        agent = BrowserAgent(sess, rec)
        for i, turn in enumerate(turns, 1):
            if len(turns) > 1:
                print(f"\n----- turn {i}/{len(turns)}: {turn}\n", flush=True)
            final = await agent.send(turn, on_event=make_printer())
        ok = bool(final.strip())
    except Exception as e:
        logging.exception("run failed")
        rec.error("run", str(e))
        ok = False
        final = f"FAILED: {e}"
    finally:
        summary = rec.finish(final, ok)
        try:
            await sess.close()
        except Exception:
            pass

    t = summary
    print("\n" + "=" * 60)
    print(f"run      : {t['run_id']}")
    print(f"wall     : {t['wall_seconds']}s")
    print(f"requests : {t['requests']}   tool calls: {t['tool_calls']}")
    print(f"tokens   : in {t['input_tokens']:,} ({t['cached_input_tokens']:,} cached)"
          f"  out {t['output_tokens']:,}")
    print(f"COST     : ${t['cost_usd']:.4f}")
    print(f"logs     : logs/{t['run_id']}/")
    print("=" * 60)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

#!/usr/bin/env python
"""Browse the query history.

    python history.py                 # recent queries
    python history.py -n 50           # more of them
    python history.py cheapest        # only queries/answers matching a term
    python history.py --show 3        # full detail for one entry
    python history.py --days          # spend per day
    python history.py --rebuild       # regenerate history/index.md
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cuaexp import history as H   # noqa: E402


def clip(s: str, n: int) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("term", nargs="*", help="filter by text in the query or answer")
    ap.add_argument("-n", type=int, default=20)
    ap.add_argument("--show", type=int, help="print one entry in full (1 = newest)")
    ap.add_argument("--days", action="store_true")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()

    if a.rebuild:
        print("wrote", H.write_index())
        return 0

    rows = list(H.search(" ".join(a.term))) if a.term else H.load()
    if not rows:
        print("no history yet -- run a task first")
        return 0

    if a.show:
        r = list(reversed(rows))[a.show - 1]
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0

    if a.days:
        by: dict[str, dict] = {}
        for r in rows:
            d = by.setdefault(r.get("when", "")[:10], {"n": 0, "cost": 0.0, "s": 0.0})
            d["n"] += 1
            d["cost"] += r.get("cost_usd", 0)
            d["s"] += r.get("seconds", 0)
        print(f"{'day':<12}{'queries':>8}{'cost':>10}{'time':>9}")
        for day in sorted(by, reverse=True):
            d = by[day]
            print(f"{day:<12}{d['n']:>8}{'$' + format(d['cost'], '.4f'):>10}{d['s']:>8.0f}s")
        print(f"{'TOTAL':<12}{len(rows):>8}"
              f"{'$' + format(sum(r.get('cost_usd', 0) for r in rows), '.4f'):>10}")
        return 0

    shown = rows[-a.n:]
    print(f"{'#':>3} {'when':<17}{'query':<46}{'tools':>6}{'ctx':>9}{'cost':>9}{'time':>8}")
    for i, r in enumerate(reversed(shown), 1):
        print(f"{i:>3} {(r.get('when') or '')[:16].replace('T', ' '):<17}"
              f"{clip(r.get('query', ''), 45):<46}"
              f"{r.get('tool_calls', 0):>6}"
              f"{r.get('max_context_tokens', 0):>9,}"
              f"{'$' + format(r.get('cost_usd', 0), '.4f'):>9}"
              f"{r.get('seconds', 0):>7.1f}s")
    total = sum(r.get("cost_usd", 0) for r in rows)
    print(f"\n{len(rows)} queries on record, ${total:.4f} total. "
          f"Detail: history/index.md, or --show N")
    return 0


if __name__ == "__main__":
    sys.exit(main())

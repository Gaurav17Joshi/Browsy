# Browsy

A browser-use agent that drives real Chrome over CDP, with a chat panel injected
into the page. **Start at [STATUS.md](STATUS.md)** — what exists, how it fits
together, and where it stands. Design rationale in [PLAN.md](PLAN.md); background
research in
[RESEARCH-browser-agent-architectures.md](RESEARCH-browser-agent-architectures.md).

The Python package is still named `cuaexp`, as are the `CUAEXP_*`
environment variables; only the project is called Browsy.

Three interaction modes are exposed as ordinary tools and the model picks per
step: **a11y refs** (default), **code** (`run_js`), **vision** (`screenshot`).

## Setup

Python 3.12+, Chrome, and an OpenAI key. Windows 11 and macOS (Apple silicon)
are both supported from this branch.

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt      # Windows
.venv/bin/python     -m pip install -r requirements.txt      # macOS / Linux
```

Or let the launcher do it: `.\run.ps1` on Windows, `./run.sh` on macOS/Linux,
each creates the venv on first use. Everything below is spelled
`.venv/Scripts/python` for Windows; on macOS/Linux it is `.venv/bin/python`.

The API key is **never copied into this repo** — only a path to it is configured.
Set `CUAEXP_KEYFILE` to point at it, or place it at `~/.cuaexp/key`.
The loader accepts either `OPENAI_API_KEY=sk-...` or a file containing just the key.

## Run

```bash
# interactive: Chrome opens with the chat panel floating over the page
.venv/Scripts/python daemon.py --start google.com     # ./run.sh --start google.com

# one-shot task, full logs
.venv/Scripts/python run_task.py "find the cheapest flight to Tokyo in March"

# several turns in one context, sharing one browser
.venv/Scripts/python run_task.py "list the top 5 stories --then open the top one"

# restrict where it may go (recommended for anything real)
.venv/Scripts/python run_task.py --allow wikipedia.org "..."
```

## How it fits together

```
Node-free Python daemon
├─ OpenAI Agents SDK ....... agent loop, tool dispatch, streaming, tracing
├─ our layer
│   ├─ cdp.py ............. CDP client (browser-level ws, flattened sessions)
│   ├─ snapshot.py ........ a11y tree + DOM sweep -> refs
│   ├─ actions.py ......... ref -> geometry -> real input events
│   ├─ session.py ......... allowlist + confirm gate
│   ├─ panel.py ........... injected Shadow-DOM chat UI
│   └─ recorder.py ........ events.jsonl / summary.json / transcript.md
└─ Chrome (own profile, --remote-debugging-port)
```

**Chrome runs on its own profile** (`.chrome-profile/`) because since Chrome 136
the debug port is ignored on the default profile. Log into a site once and the
cookie persists across runs. The folder is gitignored — it holds live sessions.

**The panel is injected**, not a second window: registered with
`Page.addScriptToEvaluateOnNewDocument`, so it re-mounts on every navigation and
new tab. It is a pure renderer; all state lives in the daemon.

## Logs

Every run writes `logs/<run-id>/`:

| file | contents |
|---|---|
| `events.jsonl` | every tool call, result, navigation, policy block, token usage |
| `summary.json` | totals: turns, tools, tokens (incl. cached), cost in USD |
| `transcript.md` | human-readable conversation, with screenshots inlined |
| `screenshots/` | every `screenshot()` the agent took; with `--shots`, also one after every click / fill / navigate |

`--shots` gives a frame-by-frame visual record of what the agent did, at the
cost of one screenshot round trip per action. Without it, only screenshots the
agent chose to take are saved.

### Query history

`logs/` is one folder per run. `history/` is the cross-run view: **one row per
question**, with the tokens, context size and cost that question actually caused
— not the run total. Written automatically at the end of every turn, so a
multi-turn conversation produces one row per turn.

| file | contents |
|---|---|
| `history/queries.jsonl` | append-only, one JSON object per query |
| `history/index.md` | readable table, newest first, plus spend per day |

```bash
python history.py              # recent queries
python history.py cheapest     # search query + answer text
python history.py --show 1     # one entry in full, incl. tools used
python history.py --days       # spend per day
python history.py --rebuild    # regenerate index.md from the jsonl
```

`max_context_tokens` is the largest single request in that turn — how big the
context actually grew. Summing across requests would just count the same cached
prefix repeatedly.

Both `history/` and `logs/` are gitignored: they contain your questions and page
content from authenticated sessions.

*(Reading these in PowerShell 5.1 needs `Get-Content -Encoding utf8` — its
default reader is ANSI and will mangle any non-ASCII character. The files
themselves are UTF-8.)*

Cost uses the GPT-5.6 price table in `config.py` and accounts for cached input
separately (10x cheaper). Usage is recorded even when a run fails.

## Browsy, and the panel

The resting state is not a window. It is **Browsy** — a small robot standing on
the page with a **+** button on his chest, and nothing else. Hover him and his
left arm reaches across and presses that button — the elbow drops, the forearm
swings up, and his eyes follow his hand. Click it and the chat unfolds out of
him, with the **+** turning into a **−**. Click again and it folds away.

Folding is purely visual. The conversation stays, and if he is working he keeps
working — the antenna keeps pulsing, the face keeps thinking, and the elapsed
time floats beside him so you can leave the chat closed and still see he is busy.

Drag Browsy and the whole thing moves with him. The chat always unfolds
**downwards and to the right**, with him standing on its top-left corner — it
never flips above him, because then he would be sitting on his own Send button.
If there is no room below, *he* moves: opening glides him to a spot where the
chat fits. Once open it resizes from **any edge or corner**, and the grip above
the compose box resizes that independently. **Esc** folds it away.

If an answer arrives while the chat is folded, a blue dot appears on him and
stays there until you look — it rides in the panel state, so it survives the
agent navigating in the meantime.

Attach files by drag-drop, paperclip, or paste — images and PDFs go to the model
directly, text files are inlined. The compose box grows as you type. Tool calls render as cards you click to expand,
and answers render their bold, code and links rather than showing raw markdown.

He reacts throughout: idle bob and blink, head-tilt with a **?** while thinking,
a scanline sweep and **!** while acting, a lightbulb hop when done, a shake with
a flat red mouth on error.

It survives navigation without flicker: the injected script carries a *seed* —
the transcript plus your panel size and position — and the daemon re-registers
it whenever that changes, so a new page paints the panel already populated at
document-start instead of appearing empty and repainting a round trip later.

It is isolated from the page in both directions. The page cannot style it (shadow
DOM) or see it (`aria-hidden` plus a snapshot exclusion, so the agent never finds
its own Send button). And the page never receives what you do inside it: mouse,
keyboard, wheel and clipboard events stop at the shadow host, so typing a space
in the chat cannot pause the video behind it. The panel's own window-level
listeners run in the **capture** phase for exactly that reason — bubble-phase
listeners would be swallowed by that same isolation.

It also holds on to focus. Sites move focus to their own search box after load,
and against a page that does it repeatedly every keystroke you typed used to
land on the page instead. The panel takes focus back while you are using it,
bounded, and stops the moment you click anything outside it. And a watchdog in
the daemon checks every two seconds that the panel and the cursor are still on
the current page, putting them back if not — four different root causes have
made the panel vanish, so the general repair is worth more than the next
specific fix.

The whole panel is kept inside the viewport on mount and on resize, so a window
that shrank since last time cannot leave the compose box off-screen. Each
injected script also carries a build stamp: a Chrome left running by an earlier
daemon still has that daemon's panel registered, and the newer build now replaces
it instead of losing the race to it.

## The virtual mouse

A visible pointer travels to each target along a cubic Bezier before clicking,
with a trail, a press animation, and a ripple on click. On by default;
`--no-cursor` turns it off.

It is not a decoration painted over the top. Python dispatches real
`Input.dispatchMouseEvent` mouseMoved events along the curve, and the injected
cursor follows by listening for those same `mousemove` events — so the two can
never drift apart, and **the page genuinely receives the hover sequence a person
would produce** (verified: 56 mousemove events and 6 real link hovers on one
cross-page sweep). Menus that open on hover, tooltips, and `:hover` styles all
behave as they would for a human.

The arc bows perpendicular to the path by an amount that scales with distance
and caps out, so long trips curve and short corrections stay near-straight.
Timing eases in and out, and long moves get a small overshoot-and-settle.

Cost: **~0.9 s per click, zero extra tokens.** Nothing is sent to the model.

## Tests

```bash
.venv/Scripts/python tests/panel_check.py             # ~2 min, its own Chrome
.venv/Scripts/python tests/panel_check.py --headless
```

89 checks against a real panel in a real Chrome, driven with synthesized input:
folding and unfolding, hover-to-point (and that the finger actually lands on the
button), drag and release, a deliberately lost mouseup, every resize edge,
typing with spaces, what the page underneath sees, wheel routing, minimise, off-screen
rescue, re-mount across navigation, a stale script from a dead daemon, Trusted
Types, focus theft, select-all-then-type (the one place macOS needs Cmd where
Windows needs Ctrl), the Bezier path of the virtual mouse, and killing the CDP
socket to watch it reconnect. Uses port 9333 and its own profile, so it never disturbs a running
daemon. Run it after touching `panel.py`, `cursor.py` or `cdp.py` — every panel
bug so far has been an interaction bug, invisible when reading the code.

## Measured on real tasks

### OSWorld tasks

Ten login-free browser tasks taken verbatim from the public OSWorld set, asked
through the chat panel the way a person asks them:
**5 done, 2 partial, 3 failed — 626s, $4.78, 80% of input tokens cached.**
Full write-up, per task, in [evals/OSWORLD-RESULTS.md](evals/OSWORLD-RESULTS.md).

```bash
.venv/Scripts/python evals/osworld_run.py          # all ten
.venv/Scripts/python evals/osworld_run.py 5 --cap 300
```

(OSWorld 2.0's own 108 tasks are not runnable here: gated datasets, mocked
self-hosted sites, a GitLab token, and desktop apps in a VM. The reasoning is at
the top of `evals/osworld_tasks.py`.)

### Internal suite

11-task suite, easy → very hard, plus solving Sokoban level 1:
**11/11 passed, $0.59 total, 236s** with the virtual mouse enabled.
Full numbers and findings in [evals/ANALYSIS.md](evals/ANALYSIS.md).

```bash
.venv/Scripts/python evals/run_suite.py          # all
.venv/Scripts/python evals/run_suite.py 3 bonus  # specific ids
```

## Security

Deliberately two controls, both enforced in code on the execution path, not in
the prompt:

1. **Domain allowlist** (`--allow`) gated at the CDP layer on top-level
   documents. This is what breaks the exfiltration chain. Not enabled at all
   when no allowlist is given — an interceptor that only ever says yes is pure
   failure surface.
2. **Confirm before irreversible actions** — an element whose accessible name
   matches `delete`/`transfer`/`confirm payment`/`buy now`/... is refused with an
   explanation rather than clicked.

Page text is wrapped as untrusted data in every snapshot. Prompt injection is not
solved here, or anywhere; the goal is bounding the blast radius.

## Known limits

- Cross-origin iframes are not in the snapshot (main frame only).
- Canvas apps (Sheets grid, Figma) need `screenshot` + `click_at`; no
  crop-and-refine grounding yet.
- No skill/action caching — every run reasons from scratch.
- Runs against its own Chrome profile, not your real logged-in one. That needs
  the MV3 extension route (PLAN.md §3).

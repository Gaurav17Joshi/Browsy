# Porting Browsy to macOS

Notes for a Claude Code session running on a Mac. This repo was built and tested
only on Windows 11; the port was carried out and verified on **macOS 26.1,
Apple silicon, Python 3.13.9, Chrome (stable)** — see "Port status" at the
bottom for what was actually run.

## What Browsy is

A browser-use agent. It launches a real Chrome with a dedicated profile and
`--remote-debugging-port`, drives it over the Chrome DevTools Protocol, and
injects two things into every page: a chat panel (the Browsy mascot, top-left,
unfolds into a conversation) and a virtual mouse cursor. The model plans; the
CDP layer clicks, types and screenshots.

Entry points:

| Command | What it does |
|---|---|
| `daemon.py` | long-running chat panel in Chrome (the normal way to use it) |
| `run_task.py "<task>"` | one-shot task, no panel interaction |
| `tests/panel_check.py --headless` | 89-check interaction suite, drives real Chrome |
| `evals/osworld_run.py` | OSWorld benchmark harness |

## The good news

Most of the code is already portable and needs no work:

- `cuaexp/chrome.py` launches Chrome with `subprocess.Popen` and a list of args,
  so paths with spaces (`/Applications/Google Chrome.app/...`) are handled.
- `cuaexp/config.py` **already lists the macOS Chrome path** in
  `CHROME_CANDIDATES`, and `find_chrome()` picks the first that exists.
- All filesystem paths go through `pathlib`; there are no `os.system` calls, no
  registry access, no `.exe` assumptions outside the launcher script.
- Screenshots are already Retina-safe. `cuaexp/actions.py` captures with
  `"scale": 1` against a clip measured in CSS pixels, so a 2x display does not
  double the coordinates. **Do not "fix" this** by multiplying by
  `devicePixelRatio` — that would break it.
- CDP itself is OS-agnostic.

## The work

### 1. `run.sh` — the only hard blocker -- DONE

`run.ps1` is PowerShell and assumes `.venv\Scripts\python.exe`. `run.sh` is now
its sibling, same interface (`--task`, `--start`, `--allow`, `--shots`,
`--headless`, plus a bare argument as the task), creating the venv from
`requirements.txt` if missing and using `.venv/bin/python`.

It is written for **bash 3.2** — the one macOS ships. That is why empty arrays
are expanded as `${cli[@]+"${cli[@]}"}`: under `set -u`, bash 3.2 treats a plain
`"${cli[@]}"` on an empty array as an unbound variable and aborts. It also
deliberately does not `set -e`: the agent logs progress to stderr and exits
non-zero on a failed task, and that should surface, not abort the script.

`run.ps1` is untouched — this repo is meant to run on both platforms from one
branch.

### 2. The keyfile default -- ALREADY DONE

`_find_keyfile()` in `cuaexp/config.py` resolves `$CUAEXP_KEYFILE` first, then
`~/.cuaexp/key`, then `~/.config/cuaexp/key`. Both defaults are correct on
macOS, so there is nothing to change -- just put the key at `~/.cuaexp/key` or
export the variable.

There is deliberately no machine-specific fallback. One existed briefly for the
original Windows box and was removed before the repository went public: a path
is not a secret, but it does tell a reader exactly where a live key sits on a
shared machine. Do not add a Darwin equivalent.

The OpenAI key deliberately lives **outside** the repo: only a path is
configured, and the key is never copied in, logged or echoed. Preserve that
property. `cuaexp/keyfile.py` accepts either a bare key or a dotenv-style
`OPENAI_API_KEY=...` line, so no parsing changes are needed.

### 3. `requirements.txt` -- DONE

There was no dependency manifest; the list existed only as a pip command inside
`run.ps1`. `requirements.txt` now holds it. These pins install clean on macOS
arm64 under Python 3.13.9 as well as 3.12.10:

```
openai-agents==0.21.1
openai==3.1.0
websockets==16.1.1
httpx==0.28.1
```

`websockets>=14` matters: `cuaexp/cdp.py` relies on `max_queue=None` and
`ping_timeout=None` to survive a page that floods the console. Do not relax
those — a bounded queue starves the pong handler and the socket dies mid-run.

### 4. Ctrl vs Cmd — the one real behavioural difference -- DONE

The model chooses the key name, and on macOS select-all/copy/paste are **Cmd**,
not Ctrl. `_mod_bits()` in `cuaexp/actions.py` now rewrites `ctrl`/`control` to
`meta` on Darwin (blanket, as recommended).

**That rewrite alone is not enough**, and this is the part the original notes did
not know about. Cmd-shortcuts are handled by the browser's editing layer, which a
raw `Input.dispatchKeyEvent` never reaches: Cmd+A arrives at the renderer as a
keydown nobody acts on. CDP's macOS-only `commands` field is the way through, so
`_key()` attaches `["selectAll"]` / `["copy"]` / `["paste"]` / `["cut"]` /
`["undo"]` / `["redo"]` (Cmd+Shift+Z → redo) to the keyDown for the meta
shortcuts that need it. `nativeVirtualKeyCode` is now sent alongside
`windowsVirtualKeyCode`.

Verified on hardware, both directions: `tests/panel_check.py` check *"ctrl+a
selects all so typing replaces the field"* types `first`, sends `ctrl+a`, inserts
`second`, and asserts the field holds `second`. With `IS_MAC` forced false the
same sequence leaves `firstsecond` — exactly the silent append this section
predicted.

### 5. `.gitattributes` -- ALREADY DONE

`* text=auto eol=lf` is already committed, and the Mac checkout arrived LF.

## Acceptance

```sh
.venv/bin/python tests/panel_check.py --headless      # must be 89/89
```

The suite drives real Chrome and asserts on rendered geometry — panel position,
the mascot's arm landing on the chest button, focus behaviour under pages that
steal focus, and CDP socket survival. If a geometric check fails on macOS, first
confirm it is not a font-metrics difference before changing panel code.

Then run it **non-headless** too and watch it: several past bugs (a drag that
never released, a panel that followed the cursor) were invisible to assertions
and obvious on screen.

## Things not to do

- Do not copy the API key into the repo, or read its contents.
- Do not cap or trim `run_js` output — this was tried, reverted at the user's
  request, and should stay out.
- Do not commit `.chrome-profile/` (752 MB and live session cookies for every
  site logged into), `logs/`, or `history/`. `.gitignore` already covers them.


## Port status (verified on macOS 26.1, Apple silicon)

| Step | State |
|---|---|
| deps install (`requirements.txt`, Python 3.13.9, arm64) | clean, no build failures |
| every `cuaexp` module imports | yes |
| `find_chrome()` resolves | `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome` |
| `tests/panel_check.py --headless` | **89/89**, three runs, identical |
| `tests/panel_check.py` (visible Chrome) | 86-88/89, flaky -- see below, not a port defect |
| end-to-end `run_task.py` against the live API | answered correctly, 13.8s, $0.016, logs + history written |
| `evals/osworld_run.py 4 6 8 10` through the chat panel | **4/4 done**, 125s, $0.21 |
| Cmd/Ctrl select-all | fixed and covered by a check |

On the OSWorld subset macOS matched or beat the Windows baseline on the same four
tasks (Windows: 3 done + 1 partial). #4 reached the same filtered listing in 5
tool calls instead of 11; #6 returned the identical answer, down to the reply
count; #10 went one better -- Windows named Super Bowl LIV correctly but admitted
the page it had open was LIII, while this run opened the LIV recap itself and
sourced the score from it.

Four things were wrong that these notes did not anticipate:

1. **The `commands` field**, above — rewriting `ctrl` to `meta` on its own still
   left Cmd+A a no-op.
2. **One check was path-dependent, not platform-dependent.** *"the agent cannot
   see its own panel"* failed because it searched the whole rendered snapshot for
   the string `Browsy`, and the checkout directory is itself called `Browsy`, so
   the `file://` fixture URL in the snapshot header matched. Nothing had leaked.
   The check now skips the `URL:`/`TITLE:` header lines. It would fail the same
   way on Windows in a folder of that name.

3. **The suite depended on the physical window size.** Chrome is asked for a
   1440x1020 window; headless gives exactly that, and macOS clamps it to the
   screen work area -- on a 1440x900 display the viewport comes out 717px tall
   instead of 933. Three checks that assert in pixels (a 200px drag, a 55px
   resize, the mascot's reach) then failed against the panel's own
   keep-inside-the-viewport clamping. `pin_viewport()` now overrides the metrics
   to `config.VIEWPORT` for the whole run, so headless and visible mean the same
   thing. It is re-applied after the window-shrink check and after a CDP
   reattach, because both drop it.

4. **The eval harness could not type into the chat panel at all**, and the same
   717px viewport is why. Every task died with `typing attempt N landed ''`.
   `type_into_panel()` in `evals/osworld_run.py` clicked the compose box without
   unfolding the panel first, and the folded box's rect sits at **y=875**:
   inside a 933px viewport, below the fold in a 717px one. So the missing unfold
   had always been a bug and a tall window had been hiding it -- nothing about
   the fix is macOS-specific. It now calls `open_chat()` when the panel is
   folded, and all four tasks ran clean afterwards. `evals/run_suite.py` drives
   `BrowserAgent` directly and never touches the panel, so it cannot hit this.

### The visible run is flaky, and it is the mouse on your desk

With the viewport pinned, `--headless` is 89/89 every time. The **visible** run
lands at 86-88/89, and a different subset fails each time -- always in the
hover / drag / cursor-tracking family.

Measured, not guessed: hovering Browsy six times in a row and logging every
mousemove the page received gives, headless, six identical `gap=12px` with one
mousemove each -- the one we sent. Visible, the same loop gives `12, 37, 12, 37,
12, 12`, and every 37 comes with *stray* mousemove events at coordinates nobody
dispatched. That is the real macOS pointer sitting over the Chrome window: it
cancels the synthesized hover, the arm retracts, and the finger check measures
mid-air.

`Input.setIgnoreInputEvents` does not help -- it suppresses CDP-dispatched events
along with real ones (verified: zero mousemoves reach the page, all six hovers
fail).

So: **`--headless` is the gate.** Run it visible to watch, as the acceptance
notes say, but keep the physical pointer off the Chrome window while it runs, and
read a pointer-family failure there as "the mouse moved", not as a regression --
confirm it headless before believing it. This is not macOS-specific; the same
would happen on Windows with the cursor parked over the window.

Python 3.12 is not required: 3.13.9 runs everything. There is no `python3.12` in
a default macOS/Homebrew install, so pinning it would have cost a toolchain
install for nothing.

Still unverified on macOS: `evals/run_suite.py`, and OSWorld tasks #1, #2, #3,
#5, #7 and #9 -- skipped on cost, #2 alone was $3.68 on Windows.

`evals/results/osworld.json` now stamps `host` and `ran` per task, and the report
says outright when its rows come from more than one machine. The store
accumulates across partial runs, so the first merged report read as a single
ten-task run when six of its ten rows were still from Windows.

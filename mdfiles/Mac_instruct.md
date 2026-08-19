# Porting Browsy to macOS

Notes for a Claude Code session running on a Mac. This repo was built and tested
only on Windows 11. Nothing here has been run on macOS — treat every claim below
as "expected", not "verified", until the test suite passes on your machine.

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
| `tests/panel_check.py --headless` | 88-check interaction suite, drives real Chrome |
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

### 1. `run.sh` — the only hard blocker

`run.ps1` is PowerShell and assumes `.venv\Scripts\python.exe`. Write a sibling
`run.sh` with the same interface: `--task`, `--start`, `--allow`, `--shots`,
`--headless`, creating the venv if missing and using `.venv/bin/python`.

Leave `run.ps1` in place and working — this repo is meant to run on both
platforms from one branch.

### 2. The keyfile default -- ALREADY DONE

`cuaexp/config.py` no longer hardcodes any machine path. `_find_keyfile()`
resolves `$CUAEXP_KEYFILE` first, then falls back to `~/.cuaexp/key` and
`~/.config/cuaexp/key`. Both defaults are correct on macOS, so there is nothing
to change -- just put the key at `~/.cuaexp/key` or export the variable.

The OpenAI key deliberately lives **outside** the repo: only a path is
configured, and the key is never copied in, logged or echoed. Preserve that
property. `cuaexp/keyfile.py` accepts either a bare key or a dotenv-style
`OPENAI_API_KEY=...` line, so no parsing changes are needed.

### 3. `requirements.txt`

There is no dependency manifest. The dependency list exists only as a pip
command inside `run.ps1`. Versions known to work, on Python 3.12.10:

```
openai-agents==0.21.1
openai==3.1.0
websockets==16.1.1
httpx==0.28.1
```

`websockets>=14` matters: `cuaexp/cdp.py` relies on `max_queue=None` and
`ping_timeout=None` to survive a page that floods the console. Do not relax
those — a bounded queue starves the pong handler and the socket dies mid-run.

### 4. Ctrl vs Cmd — the one real behavioural difference

`cuaexp/actions.py` line 37:

```python
MODS = {"ctrl": 2, "alt": 1, "shift": 8, "meta": 4, "cmd": 4}
```

Both modifiers are supported, so `press("cmd+a")` already works. The problem is
that the model chooses the key name, and on macOS select-all/copy/paste are
**Cmd**, not Ctrl. A `ctrl+a` will dispatch cleanly and silently do nothing —
the failure is invisible, which is the worst kind.

Rewrite `ctrl` to `meta` on Darwin inside `_key()`. Consider excluding the
handful of shortcuts that genuinely stay Ctrl on macOS (`ctrl+a`/`ctrl+e` as
Emacs-style line motions in text fields) only if you hit a real case; the blanket
rewrite is the better default.

This one needs verifying on hardware — write a test that types into a field,
sends select-all, then types over it, and assert the field was replaced rather
than appended.

### 5. `.gitattributes`

The Windows machine has `core.autocrlf=true` system-wide. All files are
currently LF. Add `* text=auto eol=lf` so a Mac checkout does not receive CRLF.

## Acceptance

```sh
tests/panel_check.py --headless      # must be 88/88
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

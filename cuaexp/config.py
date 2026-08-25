"""Configuration. No secrets live in this repo -- only a path to where the key is."""
from __future__ import annotations

import os
import time
from pathlib import Path

# --- Key location -----------------------------------------------------------
# The API key is NEVER copied into this project. We only hold a path to it, and
# that path is deliberately machine-neutral: this file is public, so it must not
# disclose where a real key sits on any particular machine.
#
# Resolution order: $CUAEXP_KEYFILE, then the per-user default below.
KEYFILE_CANDIDATES = [
    Path.home() / ".cuaexp" / "key",
    Path.home() / ".config" / "cuaexp" / "key",
]


def _find_keyfile() -> Path:
    override = os.environ.get("CUAEXP_KEYFILE")
    if override:
        return Path(override).expanduser()
    for c in KEYFILE_CANDIDATES:
        if c.exists():
            return c
    return KEYFILE_CANDIDATES[0]   # the path named in the "not found" error


KEYFILE = _find_keyfile()

# --- Models -----------------------------------------------------------------
# GPT-5.6 family. Luna drives. Terra and Sol are kept here as the step-ups.
#
# Note that only MODEL_DRIVER is consumed anywhere -- the split in PLAN.md where
# Luna trims snapshots and Sol takes over after repeated failure was designed but
# never built, so one model does everything. Luna costs a tenth of Terra
# ($0.20/$0.02/$1.20 against $2.00/$0.20/$12.00 per Mtok); against that, driving a
# browser is the hardest job in the system, and a weaker driver can spend the
# saving back in extra turns. Switch with CUAEXP_MODEL_DRIVER and compare on the
# eval suite rather than by impression.
MODEL_DRIVER = os.environ.get("CUAEXP_MODEL_DRIVER", "gpt-5.6-luna")
MODEL_STRONG = os.environ.get("CUAEXP_MODEL_STRONG", "gpt-5.6-terra")
MODEL_ESCALATE = os.environ.get("CUAEXP_MODEL_ESCALATE", "gpt-5.6-sol")
MODEL_CHEAP = MODEL_DRIVER          # kept for import compatibility

# USD per 1M tokens: (input, cached_input, output)
PRICING = {
    "gpt-5.6-sol": (5.00, 0.50, 30.00),
    "gpt-5.6-terra": (2.00, 0.20, 12.00),
    "gpt-5.6-luna": (0.20, 0.02, 1.20),
    "gpt-5.6": (2.00, 0.20, 12.00),
}

# --- Injected scripts -------------------------------------------------------
# Stamped into the panel and the cursor so a newer copy always beats one left
# behind in a reused Chrome by a daemon that has since exited. Fixed for the life
# of the process: re-injecting the same build into a page is then a no-op.
BUILD = int(time.time() * 1000)

# --- Browser ----------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHROME_PROFILE = PROJECT_ROOT / ".chrome-profile"
LOG_DIR = PROJECT_ROOT / "logs"
CDP_PORT = int(os.environ.get("CUAEXP_CDP_PORT", "9222"))

# Fara1.5 (see PLAN.md section 7) trains at 1440x900. Pin the viewport there now
# so the local-model step later needs no change.
VIEWPORT = (1440, 900)

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
]

# --- Local files ------------------------------------------------------------
# One fenced directory. Browsy reads untrusted page text for a living, so its
# file access is opt-in by placement: put a file in the workspace and it can be
# read, leave it anywhere else and it cannot. Writes are narrower still.
FILE_ROOT = Path(os.environ.get("CUAEXP_FILE_ROOT", PROJECT_ROOT / "workspace")).expanduser()
FILE_OUT = FILE_ROOT / "output"
# Reference material the agent is meant to read -- design guides, house style,
# anything reusable. Readable, never writable, and version controlled, unlike the
# workspace. Put nothing secret here: it is readable by a model that reads web
# pages for a living.
SKILLS_DIR = Path(os.environ.get("CUAEXP_SKILLS_DIR",
                                 PROJECT_ROOT / "Use_Cases" / "Skills")).expanduser()
READ_ROOTS = [FILE_ROOT, SKILLS_DIR]
MAX_FILE_BYTES = int(os.environ.get("CUAEXP_MAX_FILE_BYTES", str(400_000)))

# --- Limits -----------------------------------------------------------------
# A runaway brake, not a work budget. In the OSWorld run every task that
# finished used 4-34 tool calls; the only two that reached the old cap of 40
# were failures that had got stuck. 120 leaves room for genuinely long
# research tasks -- five models and a dozen YouTube comment sections runs to
# roughly 75-95 calls -- at the cost of a stuck run burning more before it
# gives up. Lower it with CUAEXP_MAX_TURNS if that trade is wrong for you.
MAX_TURNS = int(os.environ.get("CUAEXP_MAX_TURNS", "120"))
SNAPSHOT_CHAR_BUDGET = 14000   # raw tree cut-off before trimming kicks in
# Only start placeholdering stale snapshots once accumulated tool output passes
# this. Below it, a byte-stable prefix is worth more than the tokens saved,
# because cached input is 10x cheaper than fresh input. See TrimmingModel.
TRIM_THRESHOLD_CHARS = int(os.environ.get("CUAEXP_TRIM_THRESHOLD", "60000"))
STALE_SNAPSHOT_PLACEHOLDER = "[older page snapshot dropped -- call snapshot() for current state]"


def find_chrome() -> str:
    override = os.environ.get("CUAEXP_CHROME")
    if override:
        return override
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    raise RuntimeError("Chrome not found; set CUAEXP_CHROME to the executable path")

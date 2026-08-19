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
# GPT-5.6 family. Terra drives, Luna trims/extracts, Sol is the escalation.
MODEL_DRIVER = os.environ.get("CUAEXP_MODEL_DRIVER", "gpt-5.6-terra")
MODEL_CHEAP = os.environ.get("CUAEXP_MODEL_CHEAP", "gpt-5.6-luna")
MODEL_ESCALATE = os.environ.get("CUAEXP_MODEL_ESCALATE", "gpt-5.6-sol")

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

# --- Limits -----------------------------------------------------------------
MAX_TURNS = int(os.environ.get("CUAEXP_MAX_TURNS", "40"))
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

"""Launch Chrome with its own profile and CDP enabled.

Since Chrome 136 the debug port is silently ignored on the default user-data-dir,
so a dedicated profile directory is mandatory, not a preference. The profile
persists: log into a site once and the cookie is there next run.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import time
from pathlib import Path

from .config import CDP_PORT, CHROME_PROFILE, VIEWPORT, find_chrome

log = logging.getLogger("cuaexp.chrome")


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def launch(headless: bool = False, port: int = CDP_PORT,
           profile: Path = CHROME_PROFILE) -> subprocess.Popen | None:
    """Start Chrome, or reuse one already listening on the port."""
    if port_open(port):
        log.info("reusing Chrome already on port %s", port)
        return None

    profile.mkdir(parents=True, exist_ok=True)
    w, h = VIEWPORT
    args = [
        find_chrome(),
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",       # required since Chrome 136
        f"--window-size={w},{h + 120}",     # +chrome UI so the viewport lands near 1440x900
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-features=Translate,OptimizationHints",
        "--disable-popup-blocking",
        "about:blank",
    ]
    if headless:
        args.insert(1, "--headless=new")

    log.info("launching Chrome (profile=%s, port=%s)", profile, port)
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    deadline = time.time() + 30
    while time.time() < deadline:
        if port_open(port):
            return proc
        time.sleep(0.25)
    raise RuntimeError("Chrome did not open the CDP port in time")

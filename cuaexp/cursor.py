"""A visible mouse pointer that travels in human-looking curves.

Two halves that stay in sync for free:

  * Python dispatches real `Input.dispatchMouseEvent` mouseMoved events along a
    cubic Bezier. Because those are genuine trusted input events, the page also
    gets real hover states -- menus that open on hover, tooltips, :hover styles
    all behave as they would for a person.
  * An injected element listens for those same `mousemove` events and follows.
    No coordinate is ever sent twice and nothing can drift out of sync, because
    the cursor is driven by the very events the page is reacting to.

Straight-line motion is the giveaway of automation, so the path bows to one side
by an amount that scales with distance, and the timing eases in and out the way
a hand accelerates and settles.
"""
from __future__ import annotations

import asyncio
import logging
import math
import random

from .cdp import CDP, CDPError
from .config import BUILD

log = logging.getLogger("cuaexp.cursor")

CURSOR_JS = r"""
(() => {
  // Same build check as the panel: a Chrome we reuse can still be carrying a
  // cursor script registered by a daemon that has since exited, and that copy
  // runs first. Newer build wins and replaces the older one's element.
  // Only the top-level document gets a cursor. addScriptToEvaluateOnNewDocument
  // runs in EVERY frame, and window.__cuaexpBuild is per-window, so the build
  // guard below cannot see across frames: on an iframe-heavy page (Google's
  // account pages embed full-size ones) each frame mounted its own copy and the
  // user saw a second cursor. Comparing window to window.top
  // is safe cross-origin -- it is a reference check, not a property read.
  try { if (window.top !== window.self) return; } catch (e) { return; }

  const BUILD = __CUAEXP_BUILD__;
  if (window.__cuaexpCursorBuild >= BUILD) return;
  const replacing = window.__cuaexpCursorBuild !== undefined;
  window.__cuaexpCursorBuild = BUILD;
  if (replacing) {
    const old = document.getElementById('__cuaexp_cursor');
    if (old) old.remove();
  }

  // Same Trusted Types story as the panel: on sites that enforce it (YouTube)
  // a bare innerHTML assignment throws and the cursor never gets a body.
  const TT = (() => {
    if (window.__cuaexpTT !== undefined) return window.__cuaexpTT;
    try {
      window.__cuaexpTT = (window.trustedTypes && window.trustedTypes.createPolicy)
        ? window.trustedTypes.createPolicy('cuaexp', {createHTML: s => s}) : null;
    } catch (e) { window.__cuaexpTT = null; }
    return window.__cuaexpTT;
  })();
  const setHTML = (el, html) => { el.innerHTML = TT ? TT.createHTML(html) : html; };

  const mount = () => {
    if (!document.documentElement || document.getElementById('__cuaexp_cursor')) return;
    const host = document.createElement('div');
    host.id = '__cuaexp_cursor';
    host.setAttribute('aria-hidden', 'true');
    host.setAttribute('data-cuaexp-panel', '1');   // keep it out of snapshots
    host.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;z-index:2147483646;' +
                         'pointer-events:none;';
    document.documentElement.appendChild(host);
    const root = host.attachShadow({mode: 'open'});
    setHTML(root, `
      <style>
        .c { position: fixed; left: 0; top: 0; width: 26px; height: 26px;
             pointer-events: none; transform: translate(-4px,-3px);
             transition: opacity .18s; opacity: 0;
             filter: drop-shadow(0 2px 4px rgba(0,0,0,.45)); }
        .c.on { opacity: 1 }
        .c.press svg { transform: scale(.82); }
        .c svg { transition: transform .09s ease-out; transform-origin: 5px 4px }
        .ring { position: fixed; left: 0; top: 0; width: 16px; height: 16px;
                margin: -8px 0 0 -8px; border-radius: 50%; pointer-events: none;
                border: 2px solid #2f6df5; opacity: 0; }
        .ring.go { animation: rip .5s ease-out; }
        @keyframes rip { 0% { opacity: .9; transform: scale(.35) }
                         100% { opacity: 0; transform: scale(2.6) } }
        .trail { position: fixed; left:0; top:0; width: 6px; height: 6px; margin: -3px 0 0 -3px;
                 border-radius: 50%; background: #2f6df5; pointer-events: none;
                 opacity: 0; animation: fade .75s ease-out forwards; }
        @keyframes fade { 0% { opacity: .5; transform: scale(1) }
                          100% { opacity: 0; transform: scale(.3) } }
      </style>
      <div class="c" id="cur">
        <svg viewBox="0 0 26 26" width="26" height="26">
          <path d="M5 3 L5 20.5 L9.6 16.2 L12.6 22.6 L15.6 21.1 L12.6 15 L18.6 15 Z"
                fill="#fff" stroke="#14161b" stroke-width="1.6" stroke-linejoin="round"/>
        </svg>
      </div>
      <div class="ring" id="ring"></div>`);

    const cur = root.getElementById('cur'), ring = root.getElementById('ring');
    let lastTrail = 0;

    const place = (x, y) => {
      cur.style.transform = `translate(${x - 4}px, ${y - 3}px)`;
      cur.classList.add('on');
    };

    addEventListener('mousemove', e => {
      place(e.clientX, e.clientY);
      const now = performance.now();
      if (now - lastTrail > 26) {          // breadcrumbs, so the arc is visible
        lastTrail = now;
        const t = document.createElement('div');
        t.className = 'trail';
        t.style.left = e.clientX + 'px'; t.style.top = e.clientY + 'px';
        root.appendChild(t);
        setTimeout(() => t.remove(), 780);
      }
    }, true);

    addEventListener('mousedown', e => {
      cur.classList.add('press');
      ring.style.left = e.clientX + 'px'; ring.style.top = e.clientY + 'px';
      ring.classList.remove('go'); void ring.offsetWidth; ring.classList.add('go');
    }, true);
    addEventListener('mouseup', () => cur.classList.remove('press'), true);
  };

  mount();
  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', mount);
  setInterval(mount, 1000);      // mount() is idempotent
})();
"""


def _ease(t: float) -> float:
    """Ease in-out: slow to start, quick through the middle, settling at the end."""
    return 3 * t * t - 2 * t * t * t


def bezier_path(x0: float, y0: float, x1: float, y1: float,
                steps: int, rng: random.Random) -> list[tuple[float, float]]:
    """Cubic Bezier from (x0,y0) to (x1,y1), bowed to one side."""
    dx, dy = x1 - x0, y1 - y0
    dist = math.hypot(dx, dy) or 1.0

    # Perpendicular unit vector -- the direction the arc bows in.
    px, py = -dy / dist, dx / dist
    # Bow scales with distance but is capped, so long trips arc and short
    # corrections stay nearly straight, the way a real hand behaves.
    bow = min(dist * rng.uniform(0.10, 0.22), 140.0) * rng.choice((1.0, -1.0))

    # Control points at roughly 1/3 and 2/3, offset perpendicular, with a little
    # asymmetry so the curve is not a perfect symmetric bulge.
    c1x = x0 + dx * rng.uniform(0.20, 0.36) + px * bow
    c1y = y0 + dy * rng.uniform(0.20, 0.36) + py * bow
    c2x = x0 + dx * rng.uniform(0.62, 0.80) + px * bow * rng.uniform(0.35, 0.75)
    c2y = y0 + dy * rng.uniform(0.62, 0.80) + py * bow * rng.uniform(0.35, 0.75)

    pts = []
    for i in range(1, steps + 1):
        t = _ease(i / steps)
        u = 1 - t
        x = (u ** 3) * x0 + 3 * (u ** 2) * t * c1x + 3 * u * (t ** 2) * c2x + (t ** 3) * x1
        y = (u ** 3) * y0 + 3 * (u ** 2) * t * c1y + 3 * u * (t ** 2) * c2y + (t ** 3) * y1
        pts.append((x, y))
    return pts


def _js() -> str:
    return CURSOR_JS.replace("__CUAEXP_BUILD__", str(BUILD))


class VirtualMouse:
    def __init__(self, cdp: CDP, enabled: bool = True, seed: int | None = None):
        self.cdp = cdp
        self.enabled = enabled
        self.x = 40.0
        self.y = 40.0
        self.rng = random.Random(seed)
        self._installed = False
        self._script_ids: list[str] = []

    async def install(self) -> None:
        if not self.enabled or self._installed:
            return
        try:
            # Drop any previous registration first, otherwise every re-attach
            # leaves another copy behind that still runs on each new document.
            for sid in list(self._script_ids):
                try:
                    await self.cdp.page("Page.removeScriptToEvaluateOnNewDocument",
                                        {"identifier": sid}, timeout=5)
                except CDPError:
                    pass
            self._script_ids = []
            res = await self.cdp.page("Page.addScriptToEvaluateOnNewDocument",
                                      {"source": _js()}, timeout=6)
            if res.get("identifier"):
                self._script_ids.append(res["identifier"])
            await self.cdp.eval_js(_js(), await_promise=False, timeout=8)
            self._installed = True
            # The cursor stays invisible until it has seen a mousemove, so on a
            # freshly injected page it would not appear until the next click.
            # One event at its known position makes it show up straight away.
            await self._emit(self.x, self.y)
            log.info("virtual cursor installed")
        except CDPError as e:
            if "JS error" in str(e):
                log.error("CURSOR SCRIPT IS BROKEN: %s", e)
            else:
                log.debug("cursor install failed: %s", e)

    async def reinstall(self) -> None:
        self._installed = False
        await self.install()

    async def _emit(self, x: float, y: float) -> None:
        await self.cdp.send_oneway("Input.dispatchMouseEvent",
                                   {"type": "mouseMoved", "x": x, "y": y,
                                    "button": "none", "buttons": 0, "clickCount": 0},
                                   self.cdp.page_session)

    async def resync(self) -> None:
        """Re-assert the pointer's position after a page load.

        A fresh document has a freshly injected cursor that has not seen a
        mousemove yet, so it renders invisible until the next motion. One event
        at the position we already believe it is at brings it back without
        moving anything.
        """
        if not self.enabled:
            return
        try:
            await self._emit(self.x, self.y)
        except CDPError:
            pass

    async def move_to(self, x: float, y: float) -> None:
        """Travel to (x, y) along a curve, emitting real hover events on the way."""
        if not self.enabled:
            self.x, self.y = x, y
            return
        dist = math.hypot(x - self.x, y - self.y)
        if dist < 3:
            self.x, self.y = x, y
            return

        # Fitts-ish: far targets take longer, but sub-linearly. Kept under ~0.6s
        # so the motion reads as deliberate rather than sluggish -- this cost is
        # paid on every single click.
        duration = max(0.16, min(0.60, 0.13 + dist / 2200.0))
        steps = max(8, min(26, int(dist / 26) + 8))
        step_delay = duration / steps

        try:
            for px, py in bezier_path(self.x, self.y, x, y, steps, self.rng):
                await self._emit(px, py)
                await asyncio.sleep(step_delay)

            # A small correction at the end: people rarely land dead-on first try.
            if dist > 120 and self.rng.random() < 0.5:
                ox = x + self.rng.uniform(-4, 4)
                oy = y + self.rng.uniform(-4, 4)
                await self._emit(ox, oy)
                await asyncio.sleep(0.045)
                await self._emit(x, y)
        except CDPError as e:
            log.debug("cursor move interrupted: %s", e)
        finally:
            self.x, self.y = x, y

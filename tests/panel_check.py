#!/usr/bin/env python
"""End-to-end checks for the chat panel, driven with synthesized input.

    .venv\\Scripts\\python.exe tests\\panel_check.py            # visible Chrome
    .venv\\Scripts\\python.exe tests\\panel_check.py --headless

Every panel bug so far has been an interaction bug -- a drag that never ends, a
keystroke that lands on the page, a script from a dead process winning a race --
and none of them are visible by reading the file. So this drives the real panel
in a real Chrome over CDP: it presses the mouse, moves it, releases it, types,
scrolls, navigates, and then asserts what the page and the panel each ended up
with. Uses its own profile and port, so it never disturbs a running daemon.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cuaexp.cdp import CDPError                     # noqa: E402
from cuaexp.config import PROJECT_ROOT, VIEWPORT    # noqa: E402
from cuaexp.panel import Panel                      # noqa: E402
from cuaexp.recorder import Recorder                # noqa: E402
from cuaexp.session import BrowserSession           # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PROBE = (FIXTURES / "probe.html").as_uri()
PROBE_TT = (FIXTURES / "probe-tt.html").as_uri()
PROBE_FOCUS = (FIXTURES / "probe-focus.html").as_uri()
PROBE_TRAP = (FIXTURES / "probe-trap.html").as_uri()

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(name)-14s %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)


class Errors(logging.Handler):
    """Any ERROR from our own code is a test failure in itself."""
    def __init__(self):
        super().__init__(level=logging.ERROR)
        self.records: list[str] = []

    def emit(self, record):
        self.records.append(f"{record.name}: {record.getMessage()}")


class Results:
    def __init__(self):
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name, ok, detail=""):
        self.rows.append((name, "PASS" if ok else "FAIL", detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""),
              flush=True)

    def skip(self, name, why):
        self.rows.append((name, "SKIP", why))
        print(f"  SKIP  {name}   {why}", flush=True)

    def eq(self, name, got, want, tol=0.0):
        ok = abs(got - want) <= tol if isinstance(want, (int, float)) else got == want
        self.add(name, ok, f"got {got}, want {want}" + (f" +/-{tol}" if tol else ""))

    @property
    def failed(self):
        return [r for r in self.rows if r[1] == "FAIL"]


class MiniDaemon:
    """Just enough of daemon.py for the panel to behave as it does in the product."""

    def __init__(self):
        self.panel: Panel | None = None
        self.messages: list[dict] = []
        self.transcript: list[dict] = []
        self.ui: dict = {}
        self.busy_t0: float | None = None

    def on_message(self, msg: dict):
        self.messages.append(msg)
        kind = msg.get("type")
        if kind == "ready":
            return asyncio.create_task(self._on_ready())
        if kind == "ui":
            self.ui = msg.get("ui") or {}
            self.panel.update_seed(ui=self.ui)
        return None

    async def _on_ready(self):
        await self.panel.push({"type": "restore", "items": self.transcript})
        if self.busy_t0:
            await self.panel.push({"type": "busy", "on": True, "t0": self.busy_t0})

    async def say(self, obj: dict):
        self.transcript.append(obj)
        self.panel.update_seed(items=self.transcript)
        await self.panel.push(obj)

    def last(self, kind: str) -> dict | None:
        for m in reversed(self.messages):
            if m.get("type") == kind:
                return m
        return None


# --------------------------------------------------------------------- input
async def pin_viewport(cdp) -> None:
    """Force the layout viewport to VIEWPORT, whatever the real window got.

    Half these checks assert on pixels -- a drag of 200px, a resize of 55px --
    and a panel that is deliberately kept inside the viewport clamps against its
    edges. So the viewport has to be the same number every run. It is not:
    Chrome is asked for a 1440x1020 window, headless gives exactly that, and on
    macOS the window manager clamps it to the screen work area (a 1440x900
    display yields a 717px viewport). Three checks failed that way on a Mac and
    passed headless on the same machine, which is the least useful kind of
    failure. Overriding the metrics costs nothing headless and makes the visible
    run mean the same thing.

    Must be re-applied after Emulation.clearDeviceMetricsOverride and after the
    CDP session reattaches, because both drop it.
    """
    w, h = VIEWPORT
    await cdp.page("Emulation.setDeviceMetricsOverride",
                   {"width": w, "height": h, "deviceScaleFactor": 0,
                    "mobile": False})


class Input:
    def __init__(self, cdp):
        self.cdp = cdp

    async def mouse(self, type_, x, y, button="none", buttons=0, clicks=0):
        await self.cdp.page("Input.dispatchMouseEvent", {
            "type": type_, "x": float(x), "y": float(y), "button": button,
            "buttons": buttons, "clickCount": clicks}, timeout=10)

    async def move(self, x, y, buttons=0):
        await self.mouse("mouseMoved", x, y,
                         button="left" if buttons else "none", buttons=buttons)

    async def click(self, x, y):
        await self.move(x, y)
        await self.mouse("mousePressed", x, y, button="left", buttons=1, clicks=1)
        await self.mouse("mouseReleased", x, y, button="left", buttons=0, clicks=1)
        await asyncio.sleep(0.08)

    async def drag(self, x0, y0, dx, dy, steps=10, release=True):
        """Press, move in steps with the button held, and (optionally) release."""
        await self.move(x0, y0)
        await self.mouse("mousePressed", x0, y0, button="left", buttons=1, clicks=1)
        for i in range(1, steps + 1):
            await self.move(x0 + dx * i / steps, y0 + dy * i / steps, buttons=1)
            await asyncio.sleep(0.012)
        if release:
            await self.mouse("mouseReleased", x0 + dx, y0 + dy,
                             button="left", buttons=0, clicks=1)
        await asyncio.sleep(0.12)

    async def wheel(self, x, y, dy=300):
        await self.cdp.page("Input.dispatchMouseEvent", {
            "type": "mouseWheel", "x": float(x), "y": float(y), "button": "none",
            "buttons": 0, "deltaX": 0, "deltaY": float(dy)}, timeout=10)
        await asyncio.sleep(0.15)

    async def key(self, key, code, vk, text=None):
        down = {"type": "keyDown", "key": key, "code": code,
                "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
        if text is not None:
            down["text"] = text
            down["unmodifiedText"] = text
        await self.cdp.page("Input.dispatchKeyEvent", down, timeout=10)
        await self.cdp.page("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": key, "code": code,
            "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}, timeout=10)
        await asyncio.sleep(0.02)

    async def type(self, text):
        """Real key events, one per character -- not Input.insertText.

        insertText would prove nothing: the whole question is whether the page
        sees our keystrokes, and only genuine key events can leak.
        """
        for ch in text:
            if ch == " ":
                await self.key(" ", "Space", 32, " ")
            elif ch.isalpha():
                await self.key(ch, f"Key{ch.upper()}", ord(ch.upper()), ch)
            elif ch.isdigit():
                await self.key(ch, f"Digit{ch}", ord(ch), ch)
            else:
                # Punctuation has no portable code/keycode, but Blink inserts
                # whatever `text` says, so this is still a genuine key event.
                await self.key(ch, "", 0, ch)


# --------------------------------------------------------------------- helpers
async def panel_state(cdp) -> dict:
    """Everything the assertions need, read out of the shadow root in one hop."""
    return await cdp.eval_js("""(() => {
      const h = document.getElementById('__cuaexp_host');
      if (!h) return {mounted: false, reason: 'no host'};
      if (!h.shadowRoot) return {mounted: false, reason: 'no shadow root'};
      const r = h.shadowRoot;
      const p = r.getElementById('p'), wrap = r.getElementById('wrap');
      if (!p || !wrap) return {mounted: false, reason: 'no panel element'};
      const box = p.getBoundingClientRect();
      const rect = el => { if (!el) return null;
        const b = el.getBoundingClientRect();
        return {x: b.left + b.width / 2, y: b.top + b.height / 2,
                l: b.left, t: b.top, w: b.width, h: b.height}; };
      const byId = id => rect(r.getElementById(id));
      const css = (el, prop) => (el ? getComputedStyle(el)[prop] : '');
      const op = id => { const e = r.getElementById(id);
                         return e ? parseFloat(getComputedStyle(e).opacity) : 0; };
      const bd = r.getElementById('bd'), cs = getComputedStyle(p);
      const rz = {};
      r.querySelectorAll('.rz').forEach(e => { rz[e.dataset.rz] = rect(e); });
      return {mounted: true, build: window.__cuaexpBuild,
              open: wrap.classList.contains('open'),
              visible: cs.visibility !== 'hidden' && parseFloat(cs.opacity) > 0.5,
              busy: wrap.classList.contains('busy'),
              botClass: r.getElementById('bot').getAttribute('class'),
              pointing: r.getElementById('bot').classList.contains('point'),
              bubShown: op('tbub') > 0.5,
              l: box.left, t: box.top, w: box.width, h: box.height,
              dragging: wrap.classList.contains('drag') || wrap.classList.contains('sizing'),
              bot: byId('botwrap'), bub: byId('tbub'), grip: byId('grip'), rz: rz,
              finger: rect(r.querySelector('.arm-l .mitt')),
              chest: rect(r.querySelector('.chest')),
              // Guarded: a Chrome left running by an older daemon can still be
              // showing that daemon's panel for a moment, and reading a style
              // off a null element would blow up the whole helper.
              limbCore: css(r.querySelector('.arm-r .limb'), 'stroke'),
              limbCase: css(r.querySelector('.arm-r .limb-bg'), 'stroke'),
              botOpacity: css(r.getElementById('botwrap'), 'opacity'),
              dotShown: op('dot') > 0.5,
              ta: byId('in'), go: byId('go'), body: byId('bd'),
              value: r.getElementById('in').value,
              inH: r.getElementById('in').getBoundingClientRect().height,
              bodyScroll: bd ? bd.scrollTop : -1,
              bodyScrollable: bd ? bd.scrollHeight - bd.clientHeight : -1,
              texts: [...r.querySelectorAll('.m, .t b')].map(e => e.textContent),
              items: (window.__cuaexpItems || []).length};
    })()""")


async def set_chat(cdp, inp, want, tries=3):
    """Click Browsy until the chat is open (or folded). Retries on purpose: the
    agent drives the same pointer we do, so a single click can be lost."""
    st = await panel_state(cdp)
    for _ in range(tries):
        if st.get("open") == want:
            return st
        await inp.click(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.5)
        st = await panel_state(cdp)
    return st


async def open_chat(cdp, inp):
    return await set_chat(cdp, inp, True)


async def close_chat(cdp, inp):
    return await set_chat(cdp, inp, False)


async def cursor_state(cdp) -> dict:
    return await cdp.eval_js("""(() => {
      const h = document.getElementById('__cuaexp_cursor');
      if (!h || !h.shadowRoot) return {mounted: false};
      const c = h.shadowRoot.getElementById('cur');
      const m = /translate\\(([-\\d.]+)px,\\s*([-\\d.]+)px\\)/.exec(c.style.transform || '');
      return {mounted: true, visible: c.classList.contains('on'),
              x: m ? parseFloat(m[1]) + 4 : null, y: m ? parseFloat(m[2]) + 3 : null};
    })()""")


async def probe(cdp) -> dict:
    return await cdp.eval_js("window.probe ? JSON.parse(JSON.stringify(window.probe)) : null")


async def reset_probe(cdp):
    await cdp.eval_js("window.resetProbe && window.resetProbe()")


async def wait_panel(cdp, tries=40) -> dict:
    for _ in range(tries):
        try:
            st = await panel_state(cdp)
            if st and st.get("mounted"):
                return st
        except CDPError:
            pass
        await asyncio.sleep(0.25)
    return {"mounted": False, "reason": "timed out"}


# --------------------------------------------------------------------- the run
async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--keep", action="store_true", help="leave Chrome open at the end")
    ap.add_argument("--soak", type=int, default=8,
                    help="seconds of page churn to prove the CDP socket survives")
    args = ap.parse_args()

    errs = Errors()
    logging.getLogger("cuaexp").addHandler(errs)

    R = Results()
    rec = Recorder("panelcheck", "panel regression suite")
    sess = BrowserSession(rec, headless=args.headless, port=9333,
                          profile=PROJECT_ROOT / ".chrome-profile-test")
    mini = MiniDaemon()

    try:
        await sess.start()
        cdp = sess.cdp
        await pin_viewport(cdp)
        sess.on_reattach.append(lambda: pin_viewport(sess.cdp))
        inp = Input(cdp)
        mini.panel = Panel(cdp, mini.on_message)
        await mini.panel.install()
        sess.on_reattach.append(mini.panel.reinstall_for_session)
        await sess.act.navigate(PROBE)
        await asyncio.sleep(0.5)
        await mini.panel.reinstall_for_session()

        # ------------------------------------------------ 1. it is there at all
        print("\n-- mount")
        st = await wait_panel(cdp)
        R.add("panel mounts on a normal page", st["mounted"], st.get("reason", ""))
        if not st["mounted"]:
            raise SystemExit("panel never mounted; nothing else can be tested")
        R.add("panel says ready", mini.last("ready") is not None)

        # ------------------------------------------------ 1a. folded away
        # The resting state is Browsy on his own: no window, no title bar.
        print("\n-- folding")
        R.add("starts folded away, just Browsy", not st["open"] and not st["visible"],
              f"open={st['open']} visible={st['visible']}")
        R.add("Browsy is on screen and a sensible size",
              st["bot"] and 60 < st["bot"]["w"] < 130, str(st["bot"] and st["bot"]["w"]))

        # Hover is the only affordance when it is folded, so it has to fire.
        await inp.move(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.25)
        hov = await panel_state(cdp)
        R.add("hovering makes Browsy point at his button",
              "point" in hov["botClass"] and hov["pointing"], hov["botClass"])
        await inp.move(st["bot"]["x"] - 400, st["bot"]["y"] - 300)
        await asyncio.sleep(0.25)
        R.add("and he stops pointing when you leave",
              not (await panel_state(cdp))["pointing"])

        # The gesture has to actually arrive at the button. It has been wrong two
        # ways already: painted behind the torso, and swinging out into space.
        await inp.move(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.9)          # the reach takes 0.5s
        ptr = await panel_state(cdp)
        if ptr["finger"] and ptr["chest"]:
            gap = ((ptr["finger"]["x"] - ptr["chest"]["x"]) ** 2
                   + (ptr["finger"]["y"] - ptr["chest"]["y"]) ** 2) ** 0.5
            R.add("his finger lands on the button, not in mid-air",
                  gap < ptr["chest"]["w"] / 2 + 7,
                  f"{gap:.0f}px from the centre of a {ptr['chest']['w']:.0f}px button")
        else:
            R.add("his finger lands on the button, not in mid-air", False, "no finger")
        # Every limb is a dark casing under a light core. With only the light
        # stroke the arms were invisible on a white page -- which is most pages.
        R.add("the arms have a dark casing under a light core",
              "255" in ptr["limbCore"] or "233" in ptr["limbCore"],
              f"core {ptr['limbCore']}")
        R.add("and the casing is dark", ptr["limbCase"].startswith("rgb(16")
              or ptr["limbCase"].startswith("rgb(1"), f"casing {ptr['limbCase']}")
        await inp.move(st["bot"]["x"] - 400, st["bot"]["y"] - 300)
        await asyncio.sleep(0.25)

        await inp.click(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.5)
        opened = await panel_state(cdp)
        R.add("clicking Browsy unfolds the chat",
              opened["open"] and opened["visible"] and opened["w"] > 300,
              f"{opened['w']:.0f}x{opened['h']:.0f}")
        R.add("the chat lands fully on screen",
              opened["l"] >= 0 and opened["t"] >= 0
              and opened["l"] + opened["w"] <= (await cdp.eval_js("innerWidth")) + 1
              and opened["t"] + opened["h"] <= (await cdp.eval_js("innerHeight")) + 1,
              f"at ({opened['l']:.0f}, {opened['t']:.0f})")
        R.add("the plus turns into a minus", "open" in opened["botClass"],
              opened["botClass"])
        # The shape of the whole thing: chat below and to the right, Browsy
        # standing on its top-left corner. It must never flip above him.
        R.add("the chat hangs below Browsy", opened["t"] > opened["bot"]["t"],
              f"chat top {opened['t']:.0f} vs Browsy top {opened['bot']['t']:.0f}")
        R.add("and to his right, with him on the top-left corner",
              opened["l"] > opened["bot"]["l"]
              and opened["l"] < opened["bot"]["l"] + opened["bot"]["w"],
              f"chat left {opened['l']:.0f}, Browsy spans "
              f"{opened['bot']['l']:.0f}-{opened['bot']['l'] + opened['bot']['w']:.0f}")
        R.add("he overlaps its top edge rather than sitting inside it",
              opened["bot"]["t"] + opened["bot"]["h"] > opened["t"]
              and opened["bot"]["t"] + opened["bot"]["h"] < opened["t"] + 60,
              f"his feet at {opened['bot']['t'] + opened['bot']['h']:.0f}, "
              f"chat top at {opened['t']:.0f}")

        # ------------------------------------------------ 1b. the virtual mouse
        await inp.move(300, 240)
        await asyncio.sleep(0.1)
        cur = await cursor_state(cdp)
        R.add("virtual cursor is mounted", cur.get("mounted"))
        R.add("virtual cursor follows the pointer",
              cur.get("mounted") and cur.get("visible")
              and abs((cur.get("x") or 0) - 300) < 2 and abs((cur.get("y") or 0) - 240) < 2,
              f"at ({cur.get('x')}, {cur.get('y')}), want (300, 240)")
        # The panel stops mouse events at its host so the page underneath cannot
        # see them. The cursor listens in capture phase precisely so it does not
        # freeze the moment the pointer crosses the panel.
        over = (await panel_state(cdp))["bot"]
        await inp.move(over["x"], over["y"])
        await asyncio.sleep(0.1)
        cur = await cursor_state(cdp)
        R.add("virtual cursor keeps tracking over the panel",
              abs((cur.get("x") or 0) - over["x"]) < 2,
              f"cursor at {cur.get('x')}, pointer at {over['x']:.0f}")

        # ------------------------------------------------ 1c. human-ish motion
        # Record every point the pointer passes through, in the page, so nothing
        # is missed by sampling.
        await cdp.eval_js("""(() => {
          window.__track = [];
          addEventListener('mousemove', e => window.__track.push([e.clientX, e.clientY]), true);
        })()""")
        await sess.mouse.move_to(1000, 700)
        await asyncio.sleep(0.2)
        track = await cdp.eval_js("window.__track")
        R.add("the virtual mouse travels rather than teleporting", len(track) >= 8,
              f"{len(track)} points")
        if len(track) >= 8:
            (x0, y0), (x1, y1) = track[0], track[-1]
            R.add("it arrives where it was sent",
                  abs(x1 - 1000) < 2 and abs(y1 - 700) < 2, f"ended at ({x1}, {y1})")
            # Perpendicular distance from the straight line: a bezier bows away
            # from it, a linear interpolation does not.
            dx, dy = x1 - x0, y1 - y0
            span = (dx * dx + dy * dy) ** 0.5 or 1
            bow = max(abs((px - x0) * dy - (py - y0) * dx) / span for px, py in track)
            R.add("the path is curved, not a straight line", bow > 3,
                  f"max deviation {bow:.1f}px over {span:.0f}px")
        await cdp.eval_js("window.__track = []")

        # ------------------------------------------------ 2. drag, and let go
        print("\n-- drag")
        before = await panel_state(cdp)
        hd = before["bot"]
        await inp.drag(hd["x"], hd["y"], -120, -200)
        moved = await panel_state(cdp)
        R.eq("dragging Browsy moves him horizontally",
             round(moved["bot"]["l"] - before["bot"]["l"]), -120, 3)
        R.eq("dragging Browsy moves him vertically",
             round(moved["bot"]["t"] - before["bot"]["t"]), -200, 3)
        R.add("the chat travels with him",
              abs((moved["l"] - before["l"]) - (moved["bot"]["l"] - before["bot"]["l"])) < 3
              or moved["open"] is False,
              f"chat moved {moved['l'] - before['l']:.0f}, "
              f"Browsy moved {moved['bot']['l'] - before['bot']['l']:.0f}")
        R.add("drag state cleared on release", not moved["dragging"])

        # THE bug: mouseup released over the panel was swallowed by the panel's
        # own event isolation, so the drag never ended and the panel followed the
        # cursor around with no button held.
        await inp.move(moved["bot"]["x"] + 300, moved["bot"]["y"] + 250)
        await inp.move(moved["bot"]["x"] + 420, moved["bot"]["y"] + 330)
        after = await panel_state(cdp)
        R.add("panel does NOT follow the cursor after release",
              abs(after["l"] - moved["l"]) < 2 and abs(after["t"] - moved["t"]) < 2,
              f"drifted ({after['l'] - moved['l']:.0f}, {after['t'] - moved['t']:.0f})")

        # ------------------------------------------------ 3. drag again
        hd = after["bot"]
        await inp.drag(hd["x"], hd["y"], 60, 40)
        second = await panel_state(cdp)
        R.eq("a second drag still works",
             round(second["bot"]["l"] - after["bot"]["l"]), 60, 3)
        await inp.move(second["bot"]["x"] + 200, second["bot"]["y"] + 200)
        third = await panel_state(cdp)
        R.add("no drag left stacked behind it", abs(third["l"] - second["l"]) < 2)

        # ------------------------------------------------ 4. a lost mouseup
        hd = third["bot"]
        await inp.drag(hd["x"], hd["y"], 40, 30, release=False)   # button still down
        held = await panel_state(cdp)
        R.add("drag is live while the button is held", held["dragging"])
        # The mouseup has to be *lost*, not sent -- and CDP cannot simulate that:
        # while a press is outstanding Chrome swallows any mouseMoved carrying
        # buttons=0 (measured: zero of them reach a window listener). So deliver
        # the move the way the browser would in the real case -- alt-tab, release
        # outside the window -- and check the guard fires.
        await cdp.eval_js("""dispatchEvent(new MouseEvent('mousemove',
          {clientX: %d, clientY: %d, buttons: 0, bubbles: true}))"""
                          % (held["bot"]["x"] + 90, held["bot"]["y"] + 90))
        await asyncio.sleep(0.1)
        recovered = await panel_state(cdp)
        R.add("a lost mouseup ends the drag anyway", not recovered["dragging"])
        await cdp.eval_js("""dispatchEvent(new MouseEvent('mousemove',
          {clientX: %d, clientY: %d, buttons: 0, bubbles: true}))"""
                          % (recovered["bot"]["x"] + 260, recovered["bot"]["y"] + 200))
        stable = await panel_state(cdp)
        R.add("and the panel stops moving", abs(stable["l"] - recovered["l"]) < 2,
              f"drifted {stable['l'] - recovered['l']:.0f}px")
        await inp.mouse("mouseReleased", stable["bot"]["x"], stable["bot"]["y"],
                        button="left", buttons=0, clicks=1)   # tidy up the held press
        await asyncio.sleep(0.1)

        # ------------------------------------------------ 5. resize
        print("\n-- resize")
        # Park Browsy top-left first so the chat unfolds down-right and the edges
        # mean what they say.
        st = await panel_state(cdp)
        await inp.drag(st["bot"]["x"], st["bot"]["y"],
                       120 - st["bot"]["l"], 90 - st["bot"]["t"])
        base = await panel_state(cdp)
        c = base["rz"]["se"]
        await inp.drag(c["x"], c["y"], 70, 50)
        sized = await panel_state(cdp)
        R.eq("the corner handle resizes", round(sized["w"] - base["w"]), 70, 4)
        R.eq("and in both directions at once", round(sized["h"] - base["h"]), 50, 4)

        e = sized["rz"]["e"]
        await inp.drag(e["x"], e["y"], -60, 0)
        wide = await panel_state(cdp)
        R.eq("the right edge resizes width only", round(wide["w"] - sized["w"]), -60, 4)
        R.eq("and leaves the height alone", round(wide["h"] - sized["h"]), 0, 3)

        b = wide["rz"]["s"]
        await inp.drag(b["x"], b["y"], 0, 55)
        tall = await panel_state(cdp)
        R.eq("the bottom edge resizes height only", round(tall["h"] - wide["h"]), 55, 4)

        # Pulling the left edge must keep the right edge where it is.
        wedge = tall["rz"]["w"]
        right_before = tall["l"] + tall["w"]
        await inp.drag(wedge["x"], wedge["y"], 50, 0)
        pulled = await panel_state(cdp)
        R.eq("the left edge resizes from the other side",
             round(pulled["w"] - tall["w"]), -50, 5)
        R.eq("and the right edge stays put",
             round((pulled["l"] + pulled["w"]) - right_before), 0, 5)
        sized = pulled
        g = (await panel_state(cdp))["grip"]
        await inp.drag(g["x"], g["y"], 0, -45)
        grown = await panel_state(cdp)
        R.eq("grip grows the input box", round(grown["inH"] - sized["inH"]), 45, 5)
        await inp.move(grown["l"] + 50, grown["t"] + 400)
        R.add("resize stops on release",
              abs((await panel_state(cdp))["w"] - grown["w"]) < 2)

        # ------------------------------------------------ 6. typing stays inside
        print("\n-- keyboard")
        await reset_probe(cdp)
        st = await panel_state(cdp)
        await inp.click(st["ta"]["x"], st["ta"]["y"])
        await inp.type("hi there")
        st = await panel_state(cdp)
        pr = await probe(cdp)
        R.eq("text lands in the chat box, spaces included", st["value"], "hi there")
        R.eq("the page sees no keystrokes at all", pr["keydown"], 0)
        R.eq("the page sees no clicks from the panel", pr["click"], 0)
        R.eq("the page sees no mousedown from the panel", pr["mousedown"], 0)
        R.eq("the page sees no mousemove under the panel", pr["mousemove"], 0)
        scrolled = await cdp.eval_js("scrollY")
        R.eq("space did not scroll the page", scrolled, 0)

        # ------------------------------------------------ 6b. focus theft
        # Real sites move focus to their own search box shortly after load, and
        # some ad frames do it repeatedly. Whatever the page does, a sentence
        # typed into the chat has to end up in the chat.
        print("\n-- pages that steal focus")
        await sess.act.navigate(PROBE_FOCUS)
        await wait_panel(cdp)
        st = await open_chat(cdp, inp)
        await inp.click(st["ta"]["x"], st["ta"]["y"])
        await asyncio.sleep(0.5)                 # let the page steal it at least once
        await inp.type("hello there")
        st = await panel_state(cdp)
        pr = await probe(cdp)
        steals = await cdp.eval_js("window.steals")
        R.eq("typing survives a page that steals focus", st["value"], "hello there")
        R.add("and the page did not receive those keystrokes", pr["keydown"] == 0,
              f"page saw {pr['keydown']} keys, page stole focus {steals}x")
        # Clicking the page is a deliberate choice and must not be fought.
        await inp.click(60, 200)
        await asyncio.sleep(0.6)
        who = await cdp.eval_js("document.activeElement.id || document.activeElement.tagName")
        R.add("clicking the page still hands focus to the page", who != "__cuaexp_host",
              f"focus is on {who}")
        await cdp.eval_js("window.stopStealing()")

        # A modal with a focus trap is a harder case than plain focus theft: it
        # enforces focus on every change, so nothing in the same document can
        # hold it. The chat still has to receive what the user types.
        await sess.act.navigate(PROBE_TRAP)
        await wait_panel(cdp)
        st = await open_chat(cdp, inp)
        await inp.click(st["ta"]["x"], st["ta"]["y"])
        await inp.type("trapped text")
        st = await panel_state(cdp)
        pr = await probe(cdp)
        R.eq("typing survives a modal focus trap", st["value"], "trapped text")
        R.add("the trapped page got none of those keystrokes", pr["keydown"] == 0,
              f"page saw {pr['keydown']} keys after {pr['grabs']} focus grabs")

        await sess.act.navigate(PROBE)
        await wait_panel(cdp)
        st = await open_chat(cdp, inp)
        await inp.click(st["ta"]["x"], st["ta"]["y"])
        await inp.type("hi there")

        # ------------------------------------------------ 7. sending
        await inp.key("Enter", "Enter", 13)
        await asyncio.sleep(0.3)
        sent = mini.last("message")
        R.add("Enter sends the message", bool(sent) and sent.get("text") == "hi there",
              json.dumps(sent)[:90] if sent else "nothing arrived")
        R.eq("the chat box is cleared after sending", (await panel_state(cdp))["value"], "")

        # ------------------------------------------------ 8. wheel
        print("\n-- wheel and buttons")
        await reset_probe(cdp)
        for i in range(14):
            await mini.say({"type": "assistant", "text": f"filler line {i} " + "x" * 60})
        st = await panel_state(cdp)
        await inp.wheel(st["body"]["x"], st["body"]["y"], 300)
        after_wheel = await panel_state(cdp)
        page_y = await cdp.eval_js("scrollY")
        R.eq("the wheel does not scroll the page under the panel", page_y, 0)
        R.add("the wheel scrolls the chat instead", after_wheel["bodyScroll"] > 0,
              f"scrollTop {after_wheel['bodyScroll']}")
        await inp.wheel(60, 300, 300)      # over the page, away from the panel
        R.add("the wheel still scrolls the page elsewhere",
              await cdp.eval_js("scrollY") > 0)
        await cdp.eval_js("scrollTo(0, 0)")

        # ------------------------------------------------ 9. folding it away
        # Folding is meant to be purely visual: the conversation is still there
        # and, if the agent is working, it keeps working.
        st = await panel_state(cdp)
        before_texts = len(st["texts"])
        await inp.click(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.45)
        folded = await panel_state(cdp)
        R.add("clicking Browsy folds the chat away",
              not folded["open"] and not folded["visible"])
        R.add("Browsy stays put when the chat folds",
              abs(folded["bot"]["l"] - st["bot"]["l"]) < 2,
              f"moved {folded['bot']['l'] - st['bot']['l']:.0f}px")
        await inp.click(folded["bot"]["x"], folded["bot"]["y"])
        await asyncio.sleep(0.45)
        back = await panel_state(cdp)
        R.add("clicking again brings it back", back["open"] and back["visible"])
        R.eq("with the whole conversation still in it", len(back["texts"]), before_texts)

        # Escape is the keyboard version of clicking him.
        st = await open_chat(cdp, inp)
        await inp.click(st["ta"]["x"], st["ta"]["y"])
        await inp.key("Escape", "Escape", 27)
        await asyncio.sleep(0.45)
        R.add("Escape folds the chat away", not (await panel_state(cdp))["open"])

        # An answer that arrives while it is folded should be noticeable.
        await mini.say({"type": "assistant", "text": "here is what I found"})
        await asyncio.sleep(0.4)
        marked = await panel_state(cdp)
        R.add("an answer while folded marks Browsy", marked["dotShown"])
        # The state class and the dot's own class must not be the same word: when
        # they were, the wrapper matched the dot's rule and the entire robot
        # blinked out of existence.
        R.add("and marking him does not make him vanish",
              marked["mounted"] and marked["botOpacity"] == "1"
              and marked["bot"]["w"] > 60,
              f"opacity {marked['botOpacity']}, width {marked['bot'] and marked['bot']['w']}")
        # The agent navigates while you are not looking; the mark has to survive
        # that, or you never learn the answer arrived.
        await asyncio.sleep(0.5)
        await sess.act.navigate(PROBE + "?after-answer=1")
        await wait_panel(cdp)
        await asyncio.sleep(0.5)
        R.add("the mark survives the agent navigating",
              (await panel_state(cdp))["dotShown"])
        st = await open_chat(cdp, inp)
        await asyncio.sleep(0.3)
        R.add("opening it clears the mark", not (await panel_state(cdp))["dotShown"])
        st = await panel_state(cdp)
        await inp.click(st["bot"]["x"], st["bot"]["y"])
        await asyncio.sleep(0.45)

        # Working while folded away has to be visible on Browsy himself.
        mini.busy_t0 = None
        await mini.panel.push({"type": "busy", "on": True,
                               "t0": __import__("time").time() * 1000 - 3000})
        await asyncio.sleep(0.3)
        working = await close_chat(cdp, inp)
        R.add("folded away, Browsy still shows he is working",
              not working["open"] and working["busy"]
              and "think" in working["botClass"],
              f"open={working['open']} class={working['botClass']}")
        vw = await cdp.eval_js("innerWidth")
        R.add("and the elapsed time shows beside him", working["bubShown"])
        R.add("with the bubble actually on screen",
              working["bub"] and working["bub"]["l"] >= 0
              and working["bub"]["l"] + working["bub"]["w"] <= vw + 1,
              f"bubble at {working['bub'] and round(working['bub']['l'])} in {vw}")
        await mini.panel.push({"type": "busy", "on": False})
        await asyncio.sleep(0.2)
        await open_chat(cdp, inp)

        # ------------------------------------------------ 10. off-screen rescue
        print("\n-- geometry")
        st = await open_chat(cdp, inp)      # measuring a folded panel measures its
        hd = st["bot"]                      # fold animation, not its geometry
        await inp.drag(hd["x"], hd["y"], 600, 300)     # shove it to the edge
        await cdp.page("Emulation.setDeviceMetricsOverride",
                       {"width": 700, "height": 520, "deviceScaleFactor": 0,
                        "mobile": False})
        await asyncio.sleep(0.4)
        small = await panel_state(cdp)
        R.add("the whole panel stays on screen when the window shrinks",
              small["l"] >= 0 and small["t"] >= 0
              and small["l"] + small["w"] <= 701 and small["t"] + small["h"] <= 521,
              f"{small['w']:.0f}x{small['h']:.0f} at ({small['l']:.0f}, "
              f"{small['t']:.0f}) in 700x520")
        R.add("panel is not larger than the window",
              small["w"] <= 700 and small["h"] <= 520,
              f"{small['w']:.0f}x{small['h']:.0f}")
        await pin_viewport(cdp)          # back to the pinned size, not the real one
        await asyncio.sleep(0.6)
        # And it must still be usable after the window grows back -- the input
        # box hanging off the right edge is what made the chat untypeable.
        back = await panel_state(cdp)
        vw, vh = await cdp.eval_js("[innerWidth, innerHeight]")
        R.add("the chat box is still on screen after the window grows back",
              0 <= back["ta"]["l"] and back["ta"]["l"] + back["ta"]["w"] <= vw + 1
              and 0 <= back["ta"]["t"] and back["ta"]["t"] + back["ta"]["h"] <= vh + 1,
              f"input at ({back['ta']['l']:.0f}, {back['ta']['t']:.0f}) in {vw}x{vh}")

        # ------------------------------------------------ 11. across navigation
        print("\n-- navigation and re-mount")
        await mini.say({"type": "user", "text": "remember me across pages"})
        await sess.act.navigate(PROBE + "?second=1")
        st = await wait_panel(cdp)
        R.add("panel comes back after a navigation", st["mounted"])
        st = await open_chat(cdp, inp)
        R.add("the conversation comes back with it",
              any("remember me across pages" in t for t in st.get("texts", [])),
              f"{len(st.get('texts', []))} bubbles")

        # ------------------------------------------------ 11b. mid-task re-mount
        # A page that re-mounts the panel while the agent is working must come
        # back busy, with the timer still counting from when the turn started --
        # not reset to 0.0s, and not stuck with the Send button live.
        import time as _time
        t0 = _time.time() * 1000 - 4200
        mini.busy_t0 = t0
        mini.panel.update_seed(busy=True, t0=t0)
        await mini.panel.push({"type": "busy", "on": True, "t0": t0})
        await asyncio.sleep(0.5)          # let the debounced re-seed land
        await sess.act.navigate(PROBE + "?busy=1")
        await wait_panel(cdp)
        await asyncio.sleep(0.4)
        busy = await cdp.eval_js("""(() => {
          const r = document.getElementById('__cuaexp_host').shadowRoot;
          return {timer: r.getElementById('timer').textContent,
                  running: r.getElementById('timer').classList.contains('run'),
                  sendDisabled: r.getElementById('go').disabled};
        })()""")
        R.add("a re-mount mid-task comes back busy",
              busy["running"] and busy["sendDisabled"], json.dumps(busy))
        R.add("and the timer resumes instead of restarting",
              float(busy["timer"].rstrip("s")) >= 4.0, busy["timer"])
        mini.busy_t0 = None
        mini.panel.update_seed(busy=False, t0=None)
        await mini.panel.push({"type": "busy", "on": False})

        # ------------------------------------------------ 12. a stale script
        # Exactly what a Chrome left running by an earlier daemon carries: an
        # on-new-document panel from a build that no longer exists. It runs
        # first, so a boolean guard let it win.
        from cuaexp import panel as panel_mod
        stale = panel_mod._build({"items": [], "ui": {}, "busy": False, "t0": None})
        stale = stale.replace(f"const BUILD = {panel_mod.BUILD};",
                              f"const BUILD = {panel_mod.BUILD - 999};", 1)
        R.add("stale-build fixture patched correctly",
              f"const BUILD = {panel_mod.BUILD - 999};" in stale)
        old_id = (await cdp.page("Page.addScriptToEvaluateOnNewDocument",
                                 {"source": stale}))["identifier"]
        await sess.act.navigate(PROBE + "?third=1")
        st = await wait_panel(cdp)
        mounted_build = await cdp.eval_js(
            "+(document.getElementById('__cuaexp_host').dataset.build || 0)")
        R.add("a panel from a dead daemon does not win the mount race",
              st.get("build") == panel_mod.BUILD and mounted_build == panel_mod.BUILD,
              f"script build {st.get('build')}, MOUNTED build {mounted_build}, "
              f"ours {panel_mod.BUILD}")
        R.add("the live panel still answers after that",
              (await panel_state(cdp))["mounted"])
        await cdp.page("Page.removeScriptToEvaluateOnNewDocument", {"identifier": old_id})

        # ------------------------------------------------ 13. trusted types
        print("\n-- trusted types")
        await sess.act.navigate(PROBE_TT)
        st = await wait_panel(cdp)
        enforced = await cdp.eval_js("window.ttEnforced")
        if not enforced:
            R.skip("panel mounts under Trusted Types", "page did not enforce them")
        else:
            R.add("panel mounts under Trusted Types", st["mounted"], st.get("reason", ""))
        cur = await cdp.eval_js("!!document.getElementById('__cuaexp_cursor')")
        R.add("virtual cursor survives Trusted Types too", cur)

        # ------------------------------------------------ 14. invisible to agent
        print("\n-- the agent's view")
        await sess.act.navigate(PROBE)
        await open_chat(cdp, inp)
        snap = (await sess.per.capture()).render()
        # Skip the URL/TITLE header: the checkout directory is itself called
        # Browsy, so a file:// fixture URL contains the word without anything
        # having leaked. Only what we perceived *of the page* is under test.
        body = "\n".join(l for l in snap.splitlines()
                         if not l.startswith(("URL:", "TITLE:")))
        leaked = [w for w in ("Ask Browsy", "Browsy", "New context", "Send")
                  if w in body]
        R.add("the agent cannot see its own panel", not leaked, ", ".join(leaked))

        # ------------------------------------------------ 14b. select-all
        # The model writes "ctrl+a" whatever the platform. On macOS that is a
        # no-op that dispatches cleanly, so the only way to know the rewrite to
        # Cmd is working is to type over a selection and see it replaced rather
        # than appended.
        print("\n-- select-all replaces rather than appends")
        await cdp.eval_js(
            "(() => { const e = document.getElementById('pageinput');"
            " e.focus(); e.value = 'first'; })()")
        await sess.act._key("ctrl+a")
        await cdp.page("Input.insertText", {"text": "second"})
        await asyncio.sleep(0.15)
        val = await cdp.eval_js("document.getElementById('pageinput').value")
        R.add("ctrl+a selects all so typing replaces the field", val == "second",
              f"field holds {val!r}")

        # ------------------------------------------------ 15. socket endurance
        print(f"\n-- {args.soak}s of page churn")
        await cdp.eval_js("""(() => {
          window.__churn = setInterval(() => {
            for (let i = 0; i < 40; i++) {
              const d = document.createElement('div');
              d.textContent = 'churn ' + i;
              document.body.appendChild(d);
              d.remove();
            }
            fetch('/does-not-exist?' + Math.random()).catch(() => {});
          }, 20);
        })()""")
        await asyncio.sleep(args.soak)
        alive = False
        try:
            await cdp.eval_js("clearInterval(window.__churn), 1")
            alive = (await panel_state(cdp))["mounted"]
        except CDPError as e:
            R.add("CDP socket survives a busy page", False, str(e)[:80])
        else:
            R.add("CDP socket survives a busy page", alive)

        # ------------------------------------------------ 16. losing the socket
        # Everything -- panel, cursor, every click -- rides one websocket. When
        # it goes, the daemon used to keep running with nothing attached and no
        # error worth the name in the log.
        print("\n-- losing the CDP connection")
        expected_errors = len(errs.records)
        await mini.say({"type": "user", "text": "spoken before the drop"})
        before_drop = len(mini.messages)
        await cdp._ws.close()                    # Chrome closing on us, politely
        recovered = False
        for _ in range(40):
            await asyncio.sleep(0.5)
            try:
                if (await panel_state(cdp))["mounted"]:
                    recovered = True
                    break
            except Exception:
                pass
        R.add("the session reconnects on its own", recovered)
        if recovered:
            st = await open_chat(cdp, inp)
            R.add("the conversation is still there afterwards",
                  any("spoken before the drop" in t for t in st["texts"]))
            R.add("the virtual cursor comes back too",
                  (await cursor_state(cdp)).get("mounted"))
            await inp.click(st["ta"]["x"], st["ta"]["y"])
            await inp.type("still alive")
            typed = (await panel_state(cdp))["value"]
            await inp.key("Enter", "Enter", 13)
            await asyncio.sleep(0.6)
            sent = mini.last("message")
            R.add("the panel can still talk to the daemon",
                  len(mini.messages) > before_drop and sent
                  and sent.get("text") == "still alive",
                  f"typed {typed!r}, daemon last saw "
                  + (json.dumps(sent)[:70] if sent else "nothing"))
            R.add("losing the connection is logged as an error",
                  any("connection lost" in r for r in errs.records[expected_errors:]))

        unexpected = [r for i, r in enumerate(errs.records)
                      if i < expected_errors or "connection lost" not in r]
        R.add("no unexpected errors logged during the whole run", not unexpected,
              " | ".join(unexpected[:3]))

    finally:
        try:
            await sess.close()
        except Exception:
            pass
        if not args.keep and sess.proc:
            sess.proc.terminate()
        rec.finish("panel check", not R.failed)

    print("\n" + "=" * 64)
    for name, status, detail in R.rows:
        if status != "PASS":
            print(f"  {status}  {name}   {detail}")
    passed = sum(1 for r in R.rows if r[1] == "PASS")
    print(f"  {passed}/{len(R.rows)} passed, {len(R.failed)} failed")
    print("=" * 64)
    return 1 if R.failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

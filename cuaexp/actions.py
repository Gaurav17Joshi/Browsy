"""Acting on the page.

Every action resolves a semantic ref to real geometry, then dispatches a real
input event. Geometry is looked up from the layout engine, never guessed --
that is the whole advantage over coordinate-guessing from a screenshot.
We use Input.dispatch* rather than element.click() because synthetic clicks
carry isTrusted:false, skip hover/mousedown, and break on plenty of sites.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re

from .cdp import CDP, CDPError
from .snapshot import Perceiver, Ref

log = logging.getLogger("cuaexp.actions")

KEYS = {
    "Enter":      (13, "Enter", "Enter", "\r"),
    "Tab":        (9, "Tab", "Tab", "\t"),
    "Escape":     (27, "Escape", "Escape", ""),
    "Backspace":  (8, "Backspace", "Backspace", ""),
    "Delete":     (46, "Delete", "Delete", ""),
    "ArrowDown":  (40, "ArrowDown", "ArrowDown", ""),
    "ArrowUp":    (38, "ArrowUp", "ArrowUp", ""),
    "ArrowLeft":  (37, "ArrowLeft", "ArrowLeft", ""),
    "ArrowRight": (39, "ArrowRight", "ArrowRight", ""),
    "PageDown":   (34, "PageDown", "PageDown", ""),
    "PageUp":     (33, "PageUp", "PageUp", ""),
    "Home":       (36, "Home", "Home", ""),
    "End":        (35, "End", "End", ""),
    "Space":      (32, " ", "Space", " "),
}
MODS = {"ctrl": 2, "alt": 1, "shift": 8, "meta": 4, "cmd": 4}

# Models naturally write "Right" / "Up" / "Esc". Without these aliases those keys
# were silently dropped -- a whole keyboard-driven task ran while sending zero
# keys, and looked from the outside like the page was ignoring input.
ALIASES = {
    "right": "ArrowRight", "left": "ArrowLeft", "up": "ArrowUp", "down": "ArrowDown",
    "rightarrow": "ArrowRight", "leftarrow": "ArrowLeft",
    "uparrow": "ArrowUp", "downarrow": "ArrowDown",
    "esc": "Escape", "return": "Enter", "del": "Delete", "spacebar": "Space",
    " ": "Space", "pgdn": "PageDown", "pgup": "PageUp", "bksp": "Backspace",
}


def normalise_key(name: str) -> str:
    n = name.strip()
    return ALIASES.get(n.lower(), n)


class Actions:
    def __init__(self, cdp: CDP, perceiver: Perceiver, mouse=None):
        self.cdp = cdp
        self.per = perceiver
        self.mouse = mouse

    # --- geometry -----------------------------------------------------------
    async def _point(self, r: Ref) -> tuple[float, float]:
        if r.kind == "ax":
            try:
                await self.cdp.page("DOM.scrollIntoViewIfNeeded",
                                    {"backendNodeId": r.backend_node_id})
            except CDPError:
                pass
            try:
                res = await self.cdp.page("DOM.getContentQuads",
                                          {"backendNodeId": r.backend_node_id})
            except CDPError as e:
                raise RuntimeError(f"could not locate {r.ref} on the page ({e})")
            quads = res.get("quads") or []
            if not quads:
                raise RuntimeError(f"{r.ref} has no layout box (hidden or removed)")
            best, best_area = None, -1.0
            for q in quads:
                xs, ys = q[0::2], q[1::2]
                area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if area > best_area:
                    best, best_area = q, area
            xs, ys = best[0::2], best[1::2]
            return (sum(xs) / 4.0, sum(ys) / 4.0)

        # DOM-sweep ref: located by the marker attribute we set at snapshot time
        js = f"""(() => {{
          const el = document.querySelector('[data-cuaexp-x="{r.sweep_index}"]');
          if (!el) return null;
          el.scrollIntoView({{block:'center', inline:'center'}});
          const b = el.getBoundingClientRect();
          return {{x: b.x + b.width/2, y: b.y + b.height/2}};
        }})()"""
        pt = await self.cdp.eval_js(js)
        if not pt:
            raise RuntimeError(f"{r.ref} is gone from the page -- re-snapshot")
        return (pt["x"], pt["y"])

    async def _settle(self, ms: int = 700) -> None:
        await asyncio.sleep(ms / 1000)
        try:
            for _ in range(12):
                # Short timeout: readyState is instant on a healthy page, so a
                # slow answer means the renderer is busy, and waiting the full
                # default just multiplies the stall across every later call.
                state = await self.cdp.eval_js("document.readyState", timeout=5)
                if state == "complete":
                    break
                await asyncio.sleep(0.25)
        except CDPError:
            pass
        if self.mouse is not None:
            await self.mouse.resync()

    # --- actions ------------------------------------------------------------
    async def _mouse_click(self, x: float, y: float, button: str = "left") -> None:
        # Travel there first, so the page sees the same hover sequence a person
        # would produce -- some menus only open on hover, and some controls only
        # bind their handler after mouseover.
        if self.mouse is not None:
            await self.mouse.move_to(x, y)
        else:
            await self.cdp.page("Input.dispatchMouseEvent",
                                {"type": "mouseMoved", "x": x, "y": y,
                                 "button": "none", "buttons": 0, "clickCount": 0})
            await asyncio.sleep(0.03)
        # `buttons` is the bitmask of buttons currently held; Chrome's input
        # pipeline wants it set, the same way Puppeteer/Playwright do.
        for s in ({"type": "mousePressed", "button": button, "buttons": 1, "clickCount": 1},
                  {"type": "mouseReleased", "button": button, "buttons": 0, "clickCount": 1}):
            await self.cdp.page("Input.dispatchMouseEvent", {**s, "x": x, "y": y})
            await asyncio.sleep(0.04)

    async def click(self, ref: str, button: str = "left") -> str:
        r = self.per.resolve(ref)
        x, y = await self._point(r)
        before = await self.cdp.eval_js("location.href")
        await self._mouse_click(x, y, button)
        await self._settle()
        after = await self.cdp.eval_js("location.href")
        moved = " -> navigated to " + after if after != before else ""
        return f'clicked {ref} ({r.role} "{r.name}") at ({int(x)},{int(y)}){moved}'

    async def fill(self, ref: str, text: str, submit: bool = False) -> str:
        r = self.per.resolve(ref)
        x, y = await self._point(r)
        await self._mouse_click(x, y)
        await asyncio.sleep(0.08)
        # clear whatever is there, then type for real so key handlers fire
        await self.cdp.eval_js("""(() => {
            const el = document.activeElement; if (!el) return;
            if ('selectionStart' in el && el.value !== undefined) {
                el.selectionStart = 0; el.selectionEnd = (el.value || '').length;
            } else if (el.isContentEditable) {
                document.execCommand('selectAll', false, null);
            }
        })()""")
        await self.cdp.page("Input.insertText", {"text": text})
        await asyncio.sleep(0.1)
        msg = f'typed {text!r} into {ref} ({r.role} "{r.name}")'
        if submit:
            await self.press("Enter")
            msg += " and pressed Enter"
        await self._settle()
        return msg

    async def _node_object(self, r: Ref) -> str:
        """Get a JS handle for a ref so we can call methods on the real element."""
        if r.kind == "ax":
            res = await self.cdp.page("DOM.resolveNode",
                                      {"backendNodeId": r.backend_node_id})
            return res["object"]["objectId"]
        res = await self.cdp.page("Runtime.evaluate", {
            "expression": f'document.querySelector(\'[data-cuaexp-x="{r.sweep_index}"]\')'})
        oid = res.get("result", {}).get("objectId")
        if not oid:
            raise RuntimeError(f"{r.ref} is gone from the page -- re-snapshot")
        return oid

    async def select_option(self, ref: str, value: str) -> str:
        """Set a <select> by visible text or value.

        Native selects open an OS-level popup that synthesized clicks cannot
        touch, so clicking the option is a dead end -- setting .value and firing
        input/change is the reliable path. Falls back to reporting the real
        options so the model can pick a valid one instead of guessing again.
        """
        r = self.per.resolve(ref)
        oid = await self._node_object(r)
        fn = """function(want) {
            const el = this;
            if (el.tagName !== 'SELECT')
                return {ok: false, reason: 'not a <select>', tag: el.tagName};
            const opts = [...el.options].map(o => ({text: o.text.trim(), value: o.value}));
            const w = String(want).trim().toLowerCase();
            let hit = [...el.options].find(o => o.text.trim().toLowerCase() === w)
                   || [...el.options].find(o => String(o.value).toLowerCase() === w)
                   || [...el.options].find(o => o.text.trim().toLowerCase().includes(w));
            if (!hit) return {ok: false, reason: 'no matching option', options: opts};
            el.value = hit.value;
            el.dispatchEvent(new Event('input',  {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            return {ok: true, selected: hit.text.trim(), value: hit.value};
        }"""
        res = await self.cdp.page("Runtime.callFunctionOn", {
            "objectId": oid, "functionDeclaration": fn,
            "arguments": [{"value": value}], "returnByValue": True})
        out = res.get("result", {}).get("value") or {}
        await self._settle(500)
        if out.get("ok"):
            return f'selected "{out["selected"]}" in {ref} ({r.name})'
        if out.get("options"):
            avail = ", ".join(f'"{o["text"]}"' for o in out["options"][:25])
            return (f'could not select "{value}" in {ref}: no match. '
                    f'Available options: {avail}')
        return (f'{ref} is not a native <select> (it is a {out.get("tag", "?")}). '
                f'Click it to open it, snapshot, then click the option.')

    async def _key(self, key: str) -> bool:
        parts = [p.strip() for p in key.split("+")]
        base = normalise_key(parts[-1])
        mods = 0
        for m in parts[:-1]:
            mods |= MODS.get(m.lower(), 0)
        if base in KEYS:
            vk, keyname, code, text = KEYS[base]
        elif len(base) == 1:
            vk, keyname, code, text = ord(base.upper()), base, f"Key{base.upper()}", base
        else:
            return False
        for t in ("keyDown", "keyUp"):
            p = {"type": t, "windowsVirtualKeyCode": vk, "key": keyname,
                 "code": code, "modifiers": mods}
            if t == "keyDown" and text and not mods:
                p["text"] = text
            await self.cdp.page("Input.dispatchKeyEvent", p, timeout=10)
            await asyncio.sleep(0.02)
        return True

    def _known(self, key: str) -> bool:
        base = normalise_key(key.split("+")[-1].strip())
        return base in KEYS or len(base) == 1

    async def press(self, key: str) -> str:
        if not self._known(key):
            return (f"UNKNOWN KEY {key!r} -- nothing was sent. Valid names: "
                    f"{', '.join(sorted(KEYS))}, or a single character.")
        await self._key(key)
        await self._settle(400)
        return f"pressed {key}"

    async def press_sequence(self, keys: list[str], delay_ms: int = 90) -> str:
        """Send many keys in one call.

        One key per tool call means one model round trip per key, which for a
        keyboard-driven game is both painfully slow and expensive -- and the
        long settle after each key is what wedges busy pages.
        """
        delay = max(10, min(1000, delay_ms)) / 1000
        # Validate the whole sequence up front. Sending a partial sequence into a
        # game leaves the board in a state nobody can reason about -- refusing
        # outright is far easier to recover from than half a move list.
        bad = [k for k in keys if not self._known(k)]
        if bad:
            uniq = list(dict.fromkeys(bad))[:8]
            return (f"NOTHING WAS SENT -- unrecognised keys: {', '.join(uniq)}. "
                    f"Valid names: {', '.join(sorted(KEYS))} (Right/Up/Left/Down and "
                    f"Esc are accepted as aliases), or a single character. "
                    f"Re-send the whole sequence with valid names.")
        sent = 0
        for k in keys[:400]:
            try:
                await self._key(k)
                sent += 1
            except CDPError as e:
                return (f"sent {sent} of {len(keys)} keys, then the page stopped "
                        f"responding ({e}). Check the state before continuing.")
            await asyncio.sleep(delay)
        await self._settle(500)
        return f"sent {sent} keys"

    async def scroll(self, direction: str = "down", amount: int = 600) -> str:
        dy = amount if direction == "down" else -amount
        await self.cdp.eval_js(f"window.scrollBy({{top: {dy}, behavior: 'instant'}})")
        await asyncio.sleep(0.35)
        pos = await self.cdp.eval_js(
            "({y: window.scrollY, h: document.documentElement.scrollHeight, ih: window.innerHeight})")
        return (f"scrolled {direction} {abs(dy)}px "
                f"(now {pos['y']} of {max(0, pos['h'] - pos['ih'])})")

    async def navigate(self, url: str) -> str:
        if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.\-]*:", url):
            url = "https://" + url
        await self.cdp.page("Page.navigate", {"url": url})
        await self._settle(1200)
        cur = await self.cdp.eval_js("location.href")
        if cur.startswith("chrome-error"):
            return (f"navigation to {url} did not load (blocked by the domain "
                    f"policy, or the site is unreachable)")
        return f"navigated to {cur}"

    async def go_back(self) -> str:
        await self.cdp.eval_js("history.back()")
        await self._settle(1000)
        return f"went back to {await self.cdp.eval_js('location.href')}"

    async def run_js(self, code: str) -> str:
        wrapped = f"(async () => {{ {code} }})()"
        try:
            val = await self.cdp.eval_js(wrapped, timeout=30)
        except CDPError as e:
            return f"JS error: {e}"
        if val is None:
            return "(script ran, returned undefined -- remember to `return` a value)"
        import json as _json
        try:
            out = _json.dumps(val, ensure_ascii=False)
        except Exception:
            out = str(val)
        return out[:8000]

    async def screenshot(self) -> tuple[str, str]:
        """Returns (base64 png, note). Clipped at scale 1 so pixels == CSS pixels."""
        dims = await self.cdp.eval_js(
            "({w: window.innerWidth, h: window.innerHeight, dpr: window.devicePixelRatio})")
        res = await self.cdp.page("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": 0, "y": 0, "width": dims["w"], "height": dims["h"], "scale": 1},
        })
        data = res["data"]
        note = (f"viewport {dims['w']}x{dims['h']} CSS px (device pixel ratio "
                f"{dims['dpr']}; image is 1:1 with CSS pixels, so coordinates need no scaling)")
        return data, note

    async def click_xy(self, x: float, y: float) -> str:
        await self._mouse_click(x, y)
        await self._settle()
        return f"clicked at ({int(x)},{int(y)})"

    async def png_bytes(self) -> bytes:
        data, _ = await self.screenshot()
        return base64.b64decode(data)

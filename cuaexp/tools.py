"""The tool surface exposed to the model.

Three interaction modes, all as ordinary tools, and the model picks per step:
  refs   -> snapshot / click / fill / press      (default, cheap, semantic)
  code   -> run_js                               (extract, loop, compare)
  vision -> screenshot                           (canvas, dense grids)
"""
from __future__ import annotations

import base64
import logging
import time

from agents import ToolOutputImage, function_tool

from . import memory
from .config import STALE_SNAPSHOT_PLACEHOLDER
from .session import BrowserSession
from . import files as localfiles
from .files import FileDenied

log = logging.getLogger("cuaexp.tools")


def build_tools(sess: BrowserSession) -> list:
    rec = sess.rec

    def _wrap(name: str, args: dict):
        rec.tool_call(name, args)
        return time.time()

    def _done(name: str, t0: float, result: str) -> str:
        rec.tool_result(name, result, round((time.time() - t0) * 1000, 1))
        return result

    # With --shots on, capture the page after anything that can change it, so the
    # log carries a visual trail of what the agent actually did. Off by default:
    # it costs a screenshot round trip per action.
    VISUAL = {"click", "fill", "select_option", "press", "press_sequence",
              "navigate", "go_back", "click_at"}

    async def _done_shot(name: str, t0: float, result: str) -> str:
        out = _done(name, t0, result)
        if sess.trail and name in VISUAL:
            try:
                data, _ = await sess.act.screenshot()
                rec.save_image(data, f"after-{name}")
            except Exception:
                pass
        return out

    @function_tool
    async def snapshot() -> str:
        """Read the current page: interactive elements with refs, plus page text.

        Call this before acting on a page, and again after any click, navigation
        or form submission, because refs from an older snapshot stop being valid.
        """
        t0 = _wrap("snapshot", {})
        await sess.sync_active_tab()
        # A popup we auto-dismissed is often the page telling you the outcome
        # ("Level complete!"), so it belongs in the observation, not the void.
        dlg = ""
        if sess.last_dialog:
            d = sess.last_dialog
            sess.last_dialog = None
            dlg = (f'[the page showed a {d["type"]} popup, now dismissed: '
                   f'"{d["message"]}"]\n\n')
        snap = await sess.per.capture()
        if dlg and not snap.blank:
            return _done("snapshot", t0, dlg + snap.render())
        if snap.blank:
            note = await sess.recover()
            rec.log("snapshot_recovery", {"action": note})
            snap = await sess.per.capture()
            if snap.blank:
                return _done("snapshot", t0,
                             f"page still unreadable after recovery ({note})")
            return _done("snapshot", t0, f"[recovered: {note}]\n\n{snap.render()}")
        return _done("snapshot", t0, snap.render())

    @function_tool
    async def click(ref: str) -> str:
        """Click an element by its ref from the latest snapshot, e.g. "e12"."""
        t0 = _wrap("click", {"ref": ref})
        try:
            r = sess.per.resolve(ref)
        except KeyError as e:
            return _done("click", t0, f"STALE REF: {e}")
        danger = sess.confirm_needed(r.name)
        if danger:
            msg = (f"BLOCKED BY POLICY: clicking \"{r.name}\" looks irreversible "
                   f"(matched \"{danger}\"). This needs explicit user confirmation. "
                   f"Do not retry; tell the user what you want to do and why.")
            rec.log("policy_confirm_required", {"ref": ref, "name": r.name, "matched": danger})
            return _done("click", t0, msg)
        try:
            out = await sess.act.click(ref)
        except Exception as e:
            out = f"click failed: {e}"
        return await _done_shot("click", t0, out)

    @function_tool
    async def fill(ref: str, text: str, submit: bool = False) -> str:
        """Type text into a field by ref. Set submit=true to press Enter after."""
        t0 = _wrap("fill", {"ref": ref, "text": text[:120], "submit": submit})
        try:
            out = await sess.act.fill(ref, text, submit)
        except Exception as e:
            out = f"fill failed: {e}"
        return await _done_shot("fill", t0, out)

    @function_tool
    async def select_option(ref: str, value: str) -> str:
        """Choose an option in a dropdown by its visible text.

        Use this for any combobox/select rather than clicking the option --
        native selects open an OS popup that clicks cannot reach. If the value
        does not match, this returns the list of real options.
        """
        t0 = _wrap("select_option", {"ref": ref, "value": value})
        try:
            out = await sess.act.select_option(ref, value)
        except Exception as e:
            out = f"select_option failed: {e}"
        return await _done_shot("select_option", t0, out)

    @function_tool
    async def press(key: str) -> str:
        """Press a key or combo, e.g. "Enter", "Tab", "Escape", "ctrl+a", "ArrowDown"."""
        t0 = _wrap("press", {"key": key})
        try:
            out = await sess.act.press(key)
        except Exception as e:
            out = f"press failed: {e}"
        return await _done_shot("press", t0, out)

    @function_tool
    async def press_sequence(keys: list[str], delay_ms: int = 90) -> str:
        """Send a list of keys in one call, e.g. ["Right","Right","Up","Left"].

        Always prefer this over many press() calls for games, canvas apps, or any
        keyboard-driven flow -- one press per call costs a full model round trip
        each. Check the result afterwards with run_js or snapshot.
        """
        t0 = _wrap("press_sequence", {"keys": keys[:60], "n": len(keys),
                                      "delay_ms": delay_ms})
        try:
            out = await sess.act.press_sequence(keys, delay_ms)
        except Exception as e:
            out = f"press_sequence failed: {e}"
        return await _done_shot("press_sequence", t0, out)

    @function_tool
    async def scroll(direction: str = "down", amount: int = 700) -> str:
        """Scroll the page. direction is "down" or "up"; amount is in pixels."""
        t0 = _wrap("scroll", {"direction": direction, "amount": amount})
        try:
            out = await sess.act.scroll(direction, amount)
        except Exception as e:
            out = f"scroll failed: {e}"
        return await _done_shot("scroll", t0, out)

    @function_tool
    async def navigate(url: str) -> str:
        """Go to a URL in the current tab."""
        t0 = _wrap("navigate", {"url": url})
        if not sess._allowed(url):
            msg = f"BLOCKED BY POLICY: {url} is not in the allowed-domain list for this task."
            rec.log("policy_block", {"url": url, "reason": "navigate tool"})
            return _done("navigate", t0, msg)
        try:
            out = await sess.act.navigate(url)
        except Exception as e:
            out = f"navigate failed: {e}"
        return await _done_shot("navigate", t0, out)

    @function_tool
    async def go_back() -> str:
        """Go back one page in history."""
        t0 = _wrap("go_back", {})
        try:
            out = await sess.act.go_back()
        except Exception as e:
            out = f"go_back failed: {e}"
        return await _done_shot("go_back", t0, out)

    @function_tool
    async def run_js(code: str) -> str:
        """Run JavaScript in the page and return a JSON value. Use `return`.

        Far better than many clicks when you need to extract, loop, compare or
        collect. Example:
          return [...document.querySelectorAll('.plan')].map(e => ({
            name: e.querySelector('h3')?.innerText,
            price: e.querySelector('.price')?.innerText}))
        """
        t0 = _wrap("run_js", {"code": code[:600]})
        try:
            out = await sess.act.run_js(code)
        except Exception as e:
            out = f"run_js failed: {e}"
        return _done("run_js", t0, out)

    @function_tool
    async def screenshot() -> list:
        """Take a screenshot. Use only when the snapshot is not enough --
        canvas apps, dense grids, or visual layout questions."""
        t0 = _wrap("screenshot", {})
        data, note = await sess.act.screenshot()
        path = rec.save_image(data, "screenshot")
        rec.tool_result("screenshot", f"{note} -- saved {path.name}",
                        round((time.time() - t0) * 1000, 1))
        return [
            ToolOutputImage(image_url=f"data:image/png;base64,{data}", detail="high"),
            note,
        ]

    @function_tool
    async def click_at(x: int, y: int) -> str:
        """Click at viewport pixel coordinates. Only for things a snapshot cannot
        reach (canvas, custom-drawn widgets). Coordinates come from screenshot()
        and are 1:1 with CSS pixels."""
        t0 = _wrap("click_at", {"x": x, "y": y})
        try:
            out = await sess.act.click_at(x, y)
        except Exception as e:
            out = f"click_at failed: {e}"
        return await _done_shot("click_at", t0, out)

    @function_tool
    async def remember(note: str, tag: str = "") -> str:
        """Save something worth keeping beyond this conversation.

        Use for durable facts: a URL that worked, a site's quirk, a user
        preference. Give a short tag to overwrite an earlier note on the same
        subject instead of piling up duplicates. Do not store secrets.
        """
        t0 = _wrap("remember", {"note": note[:200], "tag": tag})
        return _done("remember", t0, memory.remember(note, tag))

    @function_tool
    async def read_file(path: str) -> str:
        """Read a text file from the local workspace directory.

        Two directories are readable and nothing else, a fence enforced in code
        rather than here: the workspace, where the user puts files for you, and
        the skills directory, which holds reference guides you are meant to
        follow. Pass a bare name and both are tried, e.g. read_file("notes.md")
        or read_file("web-design.md"). Passing a directory lists it, which is
        how to see what skills exist.

        If a file the user mentions is not there, say so and ask them to move it
        in. Do not try to reach it by another path; every route out is refused.
        """
        t0 = _wrap("read_file", {"path": path})
        try:
            out = localfiles.read_file(path)
        except FileDenied as e:
            out = str(e)
        except Exception as e:
            out = f"read_file failed: {e}"
        return _done("read_file", t0, out)

    @function_tool
    async def write_file(path: str, content: str) -> str:
        """Write a text file into the workspace output directory.

        Use this for anything the user should still have afterwards -- a report,
        a summary, an HTML page. Returns the path written. To show an HTML file
        you have just written, open its file:// URL with navigate().

        Writes land in the output directory only; nothing else on the machine is
        writable.
        """
        t0 = _wrap("write_file", {"path": path, "bytes": len(content or "")})
        try:
            written = localfiles.write_file(path, content or "")
            out = f"wrote {written} ({len(content or '')} chars). "
            out += f"Open it with navigate({localfiles.as_url(path)!r})"
        except FileDenied as e:
            out = str(e)
        except Exception as e:
            out = f"write_file failed: {e}"
        return _done("write_file", t0, out)

    @function_tool
    async def recall(query: str = "") -> str:
        """Look up earlier notes. Empty query returns everything recent."""
        t0 = _wrap("recall", {"query": query})
        return _done("recall", t0, memory.recall(query))

    return [snapshot, click, fill, select_option, press, press_sequence, scroll, navigate,
            go_back, run_js, screenshot, click_at, remember, recall,
            read_file, write_file]

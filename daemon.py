#!/usr/bin/env python
"""Cuaexp daemon: launch Chrome, inject the chat panel, run the agent.

    python daemon.py                      # opens a blank tab with the panel
    python daemon.py --start google.com   # opens somewhere specific
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import sys
import time

from agents import ItemHelpers, RunItemStreamEvent

from cuaexp.agent import BrowserAgent
from cuaexp.panel import Panel
from cuaexp.recorder import Recorder
from cuaexp.session import BrowserSession

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(name)-16s %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)
log = logging.getLogger("cuaexp.daemon")

# Where the accounts menu goes. AddSession, not /signin: once a session already
# exists Google bounces /signin straight to myaccount.google.com, which is not a
# login page at all. AddSession shows the account picker either way, so "sign in"
# still means sign in when you are already signed in as someone else.
LOGIN_URL = "https://accounts.google.com/AddSession"
LOGOUT_URL = "https://accounts.google.com/Logout"


class Daemon:
    def __init__(self, allow: list[str], start_url: str, trail: bool = False,
                 cursor: bool = True):
        self.rec = Recorder("session", "interactive")
        self.sess = BrowserSession(self.rec, allow=allow, trail=trail, cursor=cursor)
        self.start_url = start_url
        self.panel: Panel | None = None
        self.agent: BrowserAgent | None = None
        self.queue: asyncio.Queue = asyncio.Queue()
        self.transcript: list[dict] = []
        self.busy = False
        self.current: asyncio.Task | None = None
        self.uploads: dict[str, dict] = {}
        self.ui: dict = {}

    # --- panel plumbing -----------------------------------------------------
    def _on_panel_message(self, msg: dict):
        kind = msg.get("type")

        # File chunks are high-volume and never need the queue.
        if kind == "file_chunk":
            self._take_chunk(msg)
            return None

        # `ready` and `ui` must NOT go through the queue. The queue loop awaits a
        # whole agent turn inside handle(), so anything queued during a run waits
        # for the run to finish. A panel that re-mounts mid-task (SPA sites like
        # YouTube tear the document down and rebuild it) says `ready` and then
        # sits there empty until the task ends -- which looks exactly like the
        # chat having vanished. These are cheap and idempotent, so answer them
        # out of band, immediately.
        if kind in ("ready", "ui", "login"):
            return asyncio.create_task(self._handle_now(msg))

        return asyncio.create_task(self.queue.put(msg))

    async def _handle_now(self, msg: dict):
        try:
            if msg.get("type") == "ready":
                await self.panel.push({"type": "restore", "items": self.transcript})
                if self.busy:
                    # Pass the real start time so the timer resumes where it was
                    # rather than restarting from zero on every re-mount.
                    await self.panel.push({"type": "busy", "on": True,
                                           "t0": self.turn_t0})
            elif msg.get("type") == "ui":
                self.ui = msg.get("ui") or {}
                self.panel.update_seed(ui=self.ui)
            elif msg.get("type") == "login":
                # The user pressed sign in. Drive the browser there ourselves
                # rather than asking the agent to: no credential ever reaches the
                # model, and it costs no tokens. Out of band on purpose -- if a
                # turn is running, the button must still work.
                await self.sess.cdp.page("Page.navigate", {"url": LOGIN_URL})
                await self.push({"type": "tool", "name": "navigate",
                                 "args": json.dumps({"url": LOGIN_URL})})
            elif msg.get("type") == "logout":
                await self._logout()
        except Exception:
            log.exception("out-of-band panel message failed")

    async def _logout(self) -> None:
        """Sign out and actually delete the stored session.

        Two steps, in this order. Google's Logout endpoint first, so the session
        is revoked server-side rather than merely forgotten here -- otherwise the
        cookie we drop stays valid for anyone who has a copy. Then clear the
        browser's cookie store, which is what empties
        .chrome-profile/Default/Network/Cookies on disk.

        Deliberately clears cookies for EVERY site, not just Google: a button
        labelled "clear cookies" that quietly left other logins in place would be
        worse than one that says what it does.
        """
        try:
            await self.sess.cdp.page("Page.navigate", {"url": LOGOUT_URL})
            await asyncio.sleep(2.0)          # let the revocation round-trip
        except Exception:
            log.warning("logout navigation failed; clearing cookies anyway")
        # Network is a per-target domain, so it has to go down the page session;
        # Storage.clearCookies is browser-level and catches contexts the page
        # session cannot see.
        await self.sess.cdp.page("Network.clearBrowserCookies")
        try:
            await self.sess.cdp.send("Storage.clearCookies")
        except Exception:
            pass
        await self.sess.cdp.page("Page.navigate", {"url": "about:blank"})
        self.rec.log("logout", {"scope": "all cookies"})
        await self.push({"type": "assistant",
                         "text": "Signed out. All cookies cleared from the "
                                 "browser profile -- every site is logged out, "
                                 "not just Google."})

    def _take_chunk(self, m: dict):
        buf = self.uploads.setdefault(
            m["id"], {"name": m.get("name", "file"), "mime": m.get("mime", ""),
                      "total": m.get("total", 1), "parts": {}})
        buf["parts"][m.get("seq", 0)] = m.get("data", "")

    def _finish_upload(self, fid: str) -> dict | None:
        buf = self.uploads.pop(fid, None)
        if not buf or len(buf["parts"]) < buf["total"]:
            return None
        b64 = "".join(buf["parts"][i] for i in sorted(buf["parts"]))
        try:
            raw = base64.b64decode(b64)
        except Exception:
            return None
        updir = self.rec.dir / "uploads"
        updir.mkdir(exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_." else "-" for c in buf["name"])[:60]
        (updir / safe).write_bytes(raw)
        self.rec.log("upload", {"name": buf["name"], "mime": buf["mime"],
                                "bytes": len(raw), "saved": f"uploads/{safe}"})
        return {"name": buf["name"], "mime": buf["mime"], "b64": b64}

    async def push(self, obj: dict, remember: bool = True):
        if remember and obj.get("type") in ("user", "assistant", "tool"):
            self.transcript.append(obj)
            # Keep the injected seed current so a navigation repaints instantly
            # instead of flashing an empty panel.
            self.panel.update_seed(items=self.transcript)
        await self.panel.push(obj)

    async def stream_handler(self, ev):
        if not isinstance(ev, RunItemStreamEvent):
            return
        if ev.name == "tool_called":
            raw = getattr(ev.item, "raw_item", None)
            name = getattr(raw, "name", "?")
            args = (getattr(raw, "arguments", "") or "")
            if len(args) > 120:
                args = args[:120] + "..."
            await self.push({"type": "tool", "name": name, "args": args})
        elif ev.name == "tool_output":
            out = str(getattr(ev.item, "output", ""))[:1500]
            await self.push({"type": "tool_result", "text": out}, remember=False)
        elif ev.name == "message_output_created":
            text = ItemHelpers.text_message_output(ev.item).strip()
            if text:
                await self.push({"type": "assistant", "text": text})

    # --- main loop ----------------------------------------------------------
    async def handle(self, msg: dict):
        kind = msg.get("type")

        # "ready" and "ui" are answered out of band in _handle_now, because this
        # loop is blocked for the whole duration of a turn.

        if kind == "reset":
            self.agent.reset()
            self.transcript.clear()
            self.panel.update_seed(items=[])
            url = await self.sess.current_url()
            host = url.split("/")[2] if "://" in url else url
            await self.panel.push({"type": "clear",
                                   "text": f"New chat - still on {host}"})
            return

        if kind == "stop":
            if self.current and not self.current.done():
                self.current.cancel()
                await self.push({"type": "assistant", "text": "Stopped."})
            return

        if kind == "message":
            text = (msg.get("text") or "").strip()
            atts = [a for a in (self._finish_upload(f["id"])
                                for f in (msg.get("files") or [])) if a]
            if not text and not atts:
                return
            label = text + (("  [" + ", ".join(a["name"] for a in atts) + "]") if atts else "")
            await self.push({"type": "user", "text": label})
            self.busy = True
            self.turn_t0 = time.time() * 1000
            self.panel.update_seed(busy=True, t0=self.turn_t0)
            await self.panel.push({"type": "busy", "on": True, "t0": self.turn_t0})
            failed = False
            try:
                self.current = asyncio.create_task(
                    self.agent.send(text, on_event=self.stream_handler, attachments=atts))
                await self.current
            except asyncio.CancelledError:
                pass
            except Exception as e:
                failed = True
                log.exception("turn failed")
                self.rec.error("turn", str(e))
                await self.panel.push({"type": "error", "text": str(e)[:800]})
            finally:
                self.busy = False
                self.panel.update_seed(busy=False, t0=None)
                await self.panel.push({"type": "busy", "on": False, "error": failed})

    async def _watchdog(self):
        """The chat must never be missing. Put it back, whatever removed it.

        Root causes so far: a registration that failed mid-navigation, a page
        that wiped the DOM, a tab switch, a dropped CDP socket. Each got its own
        fix, and each was found only because a user noticed the panel gone. This
        checks every two seconds and repairs it, so the next unknown cause costs
        a two second flicker instead of a session.
        """
        while True:
            await asyncio.sleep(2.0)
            try:
                state = await self.sess.cdp.eval_js(
                    "[!!document.getElementById('__cuaexp_host'),"
                    " !!document.getElementById('__cuaexp_cursor')]", timeout=4)
            except Exception:
                continue                      # navigating, or a blocked renderer
            if not state or not state[0]:
                log.info("panel missing on this page -- reinstalling")
                try:
                    await self.panel.reinstall_for_session()
                except Exception as e:
                    log.warning("panel repair failed: %s", str(e)[:120])
            if state and not state[1]:
                try:
                    await self.sess.mouse.reinstall()
                except Exception as e:
                    log.warning("cursor repair failed: %s", str(e)[:120])

    async def run(self):
        await self.sess.start()
        self.panel = Panel(self.sess.cdp, self._on_panel_message)
        await self.panel.install()
        # Injected scripts are per-target, so a tab switch drops the panel unless
        # we re-inject. The session cannot know about the panel, so it asks.
        self.sess.on_reattach.append(self.panel.reinstall_for_session)
        self.agent = BrowserAgent(self.sess, self.rec)
        if self.start_url:
            await self.sess.act.navigate(self.start_url)
            await asyncio.sleep(0.6)
            await self.panel.reinstall_for_session()
        log.info("ready -- type in the panel inside Chrome")
        watchdog = asyncio.create_task(self._watchdog())
        try:
            while True:
                msg = await self.queue.get()
                await self.handle(msg)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            watchdog.cancel()
            s = self.rec.finish("interactive session ended", True)
            print(f"\nsession cost ${s['cost_usd']:.4f} -- logs/{s['run_id']}/")
            await self.sess.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow", default="", help="comma-separated allowed domains")
    ap.add_argument("--start", default="about:blank")
    ap.add_argument("--no-cursor", action="store_true",
                    help="disable the visible virtual mouse pointer")
    ap.add_argument("--shots", action="store_true",
                    help="save a screenshot after every page-changing action")
    args = ap.parse_args()
    allow = [d.strip() for d in args.allow.split(",") if d.strip()]
    d = Daemon(allow, args.start, trail=args.shots, cursor=not args.no_cursor)
    try:
        asyncio.run(d.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())

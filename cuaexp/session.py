"""Browser session: CDP + perception + actions + the security policy.

The allowlist and the confirm-gate live here, in code, on the execution path --
not in the prompt. That is the point: a model that gets talked into something
by injected page text still cannot get past them.
"""
from __future__ import annotations

import asyncio
import logging
import re
from pathlib import Path
from urllib.parse import urlparse

from .actions import Actions
from .cdp import CDP, CDPError
from .chrome import launch
from .config import CDP_PORT, CHROME_PROFILE
from .cursor import VirtualMouse
from .recorder import Recorder
from .snapshot import Perceiver

log = logging.getLogger("cuaexp.session")

# Matched against an element's accessible name before we will click it.
DANGER = re.compile(
    r"\b(delete|remove|transfer|withdraw|refund|cancel subscription|confirm payment|"
    r"place order|buy now|pay now|purchase|checkout|send money|deactivate|close account|"
    r"unsubscribe|sign out|log out)\b", re.I)


class BlockedByPolicy(RuntimeError):
    pass


class BrowserSession:
    def __init__(self, recorder: Recorder, allow: list[str] | None = None,
                 headless: bool = False, port: int = CDP_PORT, trail: bool = False,
                 cursor: bool = True, profile: Path = CHROME_PROFILE):
        self.rec = recorder
        self.allow = [d.lower().lstrip(".") for d in (allow or [])]
        self.headless = headless
        self.trail = trail          # save a screenshot after every page-changing action
        self.use_cursor = cursor
        self.port = port
        self.cdp = CDP(port)
        self.per: Perceiver | None = None
        self.act: Actions | None = None
        self.proc = None
        self.blocked: list[str] = []
        self.pending_confirm: str | None = None
        self.auto_confirm = False       # panel sets this when the user approves
        self._fetch_handler_installed = False
        self.last_dialog: dict | None = None
        # Anything that injects per-target state registers here. Scripts added
        # with Page.addScriptToEvaluateOnNewDocument live on ONE target, so every
        # re-attach has to redo them. The panel is owned by the daemon and not
        # reachable from here, which is exactly how it went missing after a tab
        # switch while the allowlist and cursor came back fine.
        self.on_reattach: list = []
        self._new_pages: list[str] = []
        self.profile = profile

    # --- lifecycle ----------------------------------------------------------
    async def start(self) -> None:
        self.proc = launch(headless=self.headless, port=self.port, profile=self.profile)
        await self.cdp.connect()
        await self.cdp.attach_first_page()
        self.per = Perceiver(self.cdp)
        self.mouse = VirtualMouse(self.cdp, enabled=self.use_cursor)
        await self.mouse.install()
        self.act = Actions(self.cdp, self.per, self.mouse)
        await self._install_allowlist()
        self.cdp.on_event(self._on_event)
        # A dropped socket reconnects to a *fresh* session id, so everything that
        # was injected into the old one has to be injected again.
        self.cdp.on_reconnect.append(self._reattached)
        log.info("browser session ready (allowlist=%s)", self.allow or "permissive")

    async def close(self) -> None:
        await self.cdp.close()

    async def _reattached(self) -> None:
        """Re-apply everything that is bound to a single target."""
        await self._install_allowlist()
        await self.mouse.reinstall()
        for hook in self.on_reattach:
            try:
                res = hook()
                if asyncio.iscoroutine(res):
                    await res
            except Exception:
                log.exception("re-attach hook failed")

    def _on_event(self, method: str, params: dict, sess: str | None):
        # Remember tab creation order. Picking "the newest page" off
        # Target.getTargets ordering is unreliable -- it is not creation-ordered.
        if method == "Target.targetCreated":
            info = params.get("targetInfo", {})
            if info.get("type") == "page":
                self._new_pages.append(info["targetId"])
        elif method == "Target.targetDestroyed":
            tid = params.get("targetId")
            if tid in self._new_pages:
                self._new_pages.remove(tid)

        # Follow the tab if the page opens a new one (target/window.open).
        if method == "Page.frameNavigated" and params.get("frame", {}).get("parentId") is None:
            url = params["frame"].get("url", "")
            if url and not url.startswith("about:"):
                self.rec.log("navigated", {"url": url})

        # A native alert()/confirm() blocks the renderer completely: every
        # Runtime.evaluate then times out and the tab looks dead. Nothing in the
        # page can dismiss it, because script execution is exactly what is
        # blocked -- only the protocol can. This was the cause of tabs "wedging"
        # right at the moment a task succeeded and the site said so in a popup.
        if method == "Page.javascriptDialogOpening":
            self.last_dialog = {"type": params.get("type"),
                                "message": (params.get("message") or "")[:500]}
            self.rec.log("js_dialog", self.last_dialog)
            log.info("auto-dismissing %s dialog: %s",
                     self.last_dialog["type"], self.last_dialog["message"][:120])
            return asyncio.create_task(self._dismiss_dialog(sess))

    async def _dismiss_dialog(self, sess: str | None):
        try:
            await self.cdp.send("Page.handleJavaScriptDialog",
                                {"accept": True}, sess, timeout=5)
        except CDPError as e:
            log.warning("could not dismiss dialog: %s", e)

    # --- security: domain allowlist at the CDP layer ------------------------
    def _allowed(self, url: str) -> bool:
        if not self.allow:
            return True                       # permissive mode; still logged
        host = (urlparse(url).hostname or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.allow)

    async def _install_allowlist(self) -> None:
        """Enable document-level request gating on the current page session.

        Only top-level documents are gated. Gating subresources would break
        ordinary pages (CDN, fonts, XHR) without adding protection: the
        exfiltration path we care about is a navigation to an attacker origin.

        Not enabled at all in permissive mode -- an interceptor that is only
        ever going to say yes is pure failure surface.
        """
        if not self.allow:
            return
        await self.cdp.page("Fetch.enable", {"patterns": [
            {"urlPattern": "*", "requestStage": "Request", "resourceType": "Document"}]})
        if not self._fetch_handler_installed:
            self.cdp.on_event(self._on_paused)
            self._fetch_handler_installed = True

    async def _on_paused(self, method: str, params: dict, sess: str | None):
        if method != "Fetch.requestPaused":
            return
        rid = params["requestId"]
        url = params.get("request", {}).get("url", "")
        # Reply on the session the event arrived on. Replying on the *current*
        # page session instead means that after a tab switch, requests paused on
        # the old tab are answered on the wrong session, the continue is
        # rejected, and the navigation hangs until it times out.
        try:
            if self._allowed(url):
                await self.cdp.send("Fetch.continueRequest", {"requestId": rid}, sess)
            else:
                self.blocked.append(url)
                self.rec.log("policy_block", {"url": url, "reason": "domain not in allowlist"})
                log.warning("BLOCKED navigation to %s", url[:120])
                await self.cdp.send("Fetch.failRequest",
                                    {"requestId": rid, "errorReason": "BlockedByClient"}, sess)
        except CDPError as e:
            log.warning("fetch gate failed for %s: %s", url[:80], e)

    # --- security: confirm before irreversible actions ----------------------
    def confirm_needed(self, label: str) -> str | None:
        if self.auto_confirm:
            return None
        m = DANGER.search(label or "")
        if not m:
            return None
        return m.group(0)

    # --- helpers ------------------------------------------------------------
    async def current_url(self) -> str:
        try:
            return await self.cdp.eval_js("location.href")
        except CDPError:
            return ""

    async def recover(self) -> str:
        """Unstick a tab that has stopped answering.

        Stop the pending load, then re-attach -- to another live page if this
        one is beyond help. Without this, one wedged tab times out every
        subsequent call and the whole run is lost.
        """
        try:
            await self.cdp.page("Page.stopLoading", {}, timeout=5)
        except CDPError:
            pass
        try:
            await self.cdp.eval_js("1", timeout=5)
            return "page responded after stopping the load"
        except CDPError:
            pass
        try:
            targets = (await self.cdp.send("Target.getTargets", timeout=5))["targetInfos"]
        except CDPError:
            return "browser is not responding at all"
        pages = [t for t in targets if t["type"] == "page"]
        for t in reversed(pages):
            if t["targetId"] == self.cdp.page_target:
                continue
            try:
                await self.cdp.attach(t["targetId"])
                await self._reattached()
                self.rec.log("recovered", {"url": t["url"]})
                return f"switched to another tab: {t['url'][:80]}"
            except CDPError:
                continue
        try:
            res = await self.cdp.send("Target.createTarget", {"url": "about:blank"}, timeout=8)
            await self.cdp.attach(res["targetId"])
            await self._reattached()
            self.rec.log("recovered", {"url": "about:blank (new tab)"})
            return "opened a fresh tab; navigate() to continue"
        except CDPError as e:
            return f"could not recover: {e}"

    async def sync_active_tab(self) -> None:
        """If the page opened a new tab, follow it."""
        try:
            targets = (await self.cdp.send("Target.getTargets"))["targetInfos"]
        except CDPError:
            return
        pages = {t["targetId"]: t for t in targets if t["type"] == "page"}
        if not pages:
            return

        target = None
        if self.cdp.page_target not in pages:
            # our tab is gone -- take the most recently opened survivor
            target = next((pages[t] for t in reversed(self._new_pages) if t in pages),
                          None) or list(pages.values())[-1]
        else:
            # follow a tab opened after ours (target=_blank, window.open)
            fresh = [t for t in self._new_pages
                     if t in pages and t != self.cdp.page_target
                     and pages[t]["url"].startswith("http")]
            if fresh:
                target = pages[fresh[-1]]

        if not target:
            return
        await self.cdp.attach(target["targetId"])
        await self._reattached()
        self.rec.log("tab_switch", {"url": target["url"]})
        await asyncio.sleep(0.4)

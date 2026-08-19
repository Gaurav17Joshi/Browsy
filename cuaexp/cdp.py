"""Minimal async Chrome DevTools Protocol client.

Browser-level websocket with flattened sessions, so one connection covers every
tab. Everything the agent does to the page goes through here.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import httpx
import websockets

log = logging.getLogger("cuaexp.cdp")


class CDPError(RuntimeError):
    pass


class CDP:
    def __init__(self, port: int):
        self.port = port
        self._ws: Any = None
        self._id = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: list[Callable[[str, dict, str | None], Any]] = []
        self._reader: asyncio.Task | None = None
        self.page_session: str | None = None
        self.page_target: str | None = None
        # Called after the socket has been re-established, so whatever is bound
        # to a target (panel, cursor, allowlist) can be put back.
        self.on_reconnect: list[Callable[[], Any]] = []
        self._closing = False

    # --- connection ---------------------------------------------------------
    async def connect(self, timeout: float = 30.0) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        url = None
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as c:
                    r = await c.get(f"http://127.0.0.1:{self.port}/json/version")
                    url = r.json()["webSocketDebuggerUrl"]
                    break
            except Exception:
                await asyncio.sleep(0.3)
        if not url:
            raise CDPError(f"Chrome CDP not reachable on port {self.port}")
        # max_queue=None and ping_timeout=None on purpose. A busy page can emit
        # CDP events faster than this process consumes them; with a bounded
        # queue the library stops reading the socket to apply backpressure, and
        # since pongs arrive on that same socket the keepalive then times out and
        # kills a perfectly healthy connection ("no close frame received or
        # sent"). Everything downstream of that -- the panel, the cursor, every
        # click -- is dead, silently. Unbounded queue, no pong deadline.
        self._ws = await websockets.connect(
            url, max_size=200 * 1024 * 1024, max_queue=None,
            ping_interval=20, ping_timeout=None, close_timeout=2)
        self._reader = asyncio.create_task(self._read_loop())
        await self.send("Target.setDiscoverTargets", {"discover": True})

    async def _read_loop(self) -> None:
        try:
            async for raw in self._ws:
                msg = json.loads(raw)
                mid = msg.get("id")
                if mid is not None:
                    fut = self._pending.pop(mid, None)
                    if fut and not fut.done():
                        if "error" in msg:
                            fut.set_exception(CDPError(json.dumps(msg["error"])))
                        else:
                            fut.set_result(msg.get("result", {}))
                else:
                    method = msg.get("method", "")
                    params = msg.get("params", {})
                    sess = msg.get("sessionId")
                    for h in list(self._handlers):
                        try:
                            res = h(method, params, sess)
                            if asyncio.iscoroutine(res):
                                asyncio.create_task(res)
                        except Exception:
                            log.exception("cdp handler failed")
            # A *clean* close ends the iteration with no exception at all --
            # Chrome saying "going away" looks exactly like a normal return. Not
            # handling that meant the one case where Chrome told us politely was
            # the one case we never recovered from.
            self._lost(CDPError("connection closed by Chrome"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self._lost(e)

    def _lost(self, e: Exception) -> None:
        if self._closing:
            return
        # Not a warning: from here on nothing works at all, and the old message
        # ("read loop ended") read like a shutdown notice rather than a failure.
        log.error("cdp connection lost: %s -- reconnecting", e)
        self._fail_pending(e)
        asyncio.create_task(self._reconnect())

    def _fail_pending(self, exc: Exception) -> None:
        """Nobody is going to answer these now; do not make callers wait 45s."""
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(CDPError(f"cdp disconnected: {exc}"))
        self._pending.clear()

    async def _reconnect(self) -> None:
        for attempt in range(1, 7):
            await asyncio.sleep(min(0.5 * 2 ** (attempt - 1), 5.0))
            if self._closing:
                return
            try:
                await self.connect(timeout=10)
                if self.page_target:
                    try:
                        await self.attach(self.page_target)
                    except CDPError:
                        await self.attach_first_page()
                else:
                    await self.attach_first_page()
                log.info("cdp reconnected (attempt %d)", attempt)
                for hook in list(self.on_reconnect):
                    try:
                        res = hook()
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        log.exception("reconnect hook failed")
                return
            except Exception as e:
                log.warning("cdp reconnect attempt %d failed: %s", attempt, e)
        log.error("cdp could not be re-established -- restart the daemon")

    def on_event(self, handler: Callable[[str, dict, str | None], Any]) -> None:
        self._handlers.append(handler)

    async def send(self, method: str, params: dict | None = None,
                   session_id: str | None = None, timeout: float = 45.0) -> dict:
        self._id += 1
        mid = self._id
        payload: dict[str, Any] = {"id": mid, "method": method, "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        fut: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[mid] = fut
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as e:
            self._pending.pop(mid, None)
            raise CDPError(f"cdp send failed: {e}") from e
        try:
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            self._pending.pop(mid, None)
            raise CDPError(f"timeout: {method}")

    # --- page session -------------------------------------------------------
    async def attach_first_page(self) -> str:
        """Attach to a page target, preferring a real http(s) tab."""
        targets = (await self.send("Target.getTargets"))["targetInfos"]
        pages = [t for t in targets if t["type"] == "page"]
        if not pages:
            raise CDPError("no page targets")
        pages.sort(key=lambda t: (not t["url"].startswith("http"), t["url"].startswith("devtools")))
        return await self.attach(pages[0]["targetId"])

    async def attach(self, target_id: str) -> str:
        res = await self.send("Target.attachToTarget", {"targetId": target_id, "flatten": True})
        sid = res["sessionId"]
        self.page_session = sid
        self.page_target = target_id
        # Network is deliberately NOT enabled: nothing here consumes its events,
        # and on a media-heavy page it produces thousands of them a minute --
        # pure load on the one socket everything else depends on.
        for domain in ("Page", "DOM", "Runtime"):
            try:
                await self.send(f"{domain}.enable", {}, sid)
            except CDPError:
                pass

        # Chrome silently DROPS synthesized input events for a page whose
        # visibilityState is "hidden" -- which is what a window we launched in
        # the background is. Input.dispatchMouseEvent then succeeds at the
        # protocol level while the renderer never sees a thing, so clicks look
        # like they worked and nothing happens. Page.bringToFront does not fix
        # it. Focus emulation does: it makes the renderer treat the page as
        # focused regardless of what the OS window is doing.
        for method, params in (
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
            ("Page.setWebLifecycleState", {"state": "active"}),
        ):
            try:
                await self.send(method, params, sid)
            except CDPError as e:
                log.debug("%s unavailable: %s", method, e)
        # Accessibility must be enabled AND primed -- Chrome builds the tree
        # lazily, so the first getFullAXTree can otherwise come back empty.
        try:
            await self.send("Accessibility.enable", {}, sid)
            await self.send("Accessibility.getFullAXTree", {"depth": 1}, sid)
        except CDPError:
            pass
        return sid

    async def send_oneway(self, method: str, params: dict | None = None,
                          session_id: str | None = None) -> None:
        """Fire a command without waiting for its reply.

        For a stream of mouseMoved events the reply carries nothing we need, and
        awaiting each one puts a full round trip between every step -- which
        turns smooth cursor motion into a visible stutter. Chrome processes
        commands in the order they arrive, so ordering still holds.
        """
        self._id += 1
        payload: dict[str, Any] = {"id": self._id, "method": method,
                                   "params": params or {}}
        if session_id:
            payload["sessionId"] = session_id
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as e:
            raise CDPError(f"cdp send failed: {e}") from e

    # --- convenience --------------------------------------------------------
    async def page(self, method: str, params: dict | None = None, timeout: float = 45.0) -> dict:
        if not self.page_session:
            raise CDPError("no page attached")
        return await self.send(method, params, self.page_session, timeout)

    async def eval_js(self, expression: str, await_promise: bool = True, timeout: float = 45.0) -> Any:
        res = await self.page("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": await_promise,
            "userGesture": True,
        }, timeout=timeout)
        if res.get("exceptionDetails"):
            det = res["exceptionDetails"]
            msg = det.get("exception", {}).get("description") or det.get("text")
            raise CDPError(f"JS error: {msg}")
        return res.get("result", {}).get("value")

    async def close(self) -> None:
        self._closing = True
        if self._reader:
            self._reader.cancel()
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass

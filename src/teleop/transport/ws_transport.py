"""WebSocket transport — control plane over a single cloud WebSocket.

This is the "works on any HTTP/WS host (Render, Railway, Fly, …) for free"
transport. Instead of a raw-TCP Zenoh router, leader and follower each open a
WebSocket to the FastAPI signaling server and exchange control messages through
it (star topology relay). Same ``Transport`` interface as inproc/Zenoh, so the
follower/leader code is unchanged.

Trade-off vs Zenoh: traffic hairpins through the cloud server (one extra hop of
latency) instead of going peer-to-peer. Perfectly fine for testing and low-rate
teleoperation; switch to Zenoh for production low-latency P2P.

Messages on the wire:  {"type": "pub", "key": <topic>, "payload": <dict>}
The server broadcasts each to the other peers in the same session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Dict, List

from .base import Transport, Handler

log = logging.getLogger(__name__)


class WebSocketTransport(Transport):
    def __init__(self, url: str, session_id: str, peer_id: str,
                 role: str = "viewer", connect_timeout: float = 10.0) -> None:
        try:
            import websockets  # noqa: F401
        except ImportError as e:  # pragma: no cover
            raise RuntimeError(
                "websockets is required for the WebSocket transport. "
                "Install with: pip install -e '.[api]'"
            ) from e
        # Normalize http(s):// to ws(s):// for convenience.
        url = url.replace("https://", "wss://").replace("http://", "ws://")
        self.url = url.rstrip("/") + f"/ws/{session_id}/{peer_id}"
        self.role = role
        self._subs: Dict[str, List[Handler]] = {}
        self._ws = None
        self._running = True
        self._connected = threading.Event()
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="ws-tx", daemon=True)
        self._thread.start()
        if not self._connected.wait(timeout=connect_timeout):
            log.warning("WebSocket transport not connected after %.0fs (will keep retrying)",
                        connect_timeout)

    # ---- background asyncio loop ------------------------------------------
    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        finally:
            self._loop.close()

    async def _main(self) -> None:
        import websockets
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(self.url, max_size=None) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({"type": "join", "role": self.role}))
                    self._connected.set()
                    log.info("WebSocket transport connected: %s", self.url)
                    backoff = 1.0
                    async for raw in ws:
                        self._dispatch(raw)
            except asyncio.CancelledError:
                break
            except Exception:
                self._ws = None
                if not self._running:
                    break
                log.warning("WebSocket transport reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
        self._ws = None

    def _dispatch(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if msg.get("type") != "pub":
            return  # ignore signaling/telemetry frames on the control channel
        key = msg.get("key")
        payload = msg.get("payload")
        for h in list(self._subs.get(key, ())):
            try:
                h(payload)
            except Exception:
                log.exception("ws subscriber error on %s", key)

    # ---- Transport API ----------------------------------------------------
    def publish(self, key: str, payload: Dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return  # dropped while disconnected; never raise into control loop
        data = json.dumps({"type": "pub", "key": key, "payload": payload})
        try:
            asyncio.run_coroutine_threadsafe(ws.send(data), self._loop)
        except Exception:
            log.debug("ws publish dropped on %s", key)

    def subscribe(self, key: str, handler: Handler) -> None:
        self._subs.setdefault(key, []).append(handler)

    def close(self) -> None:
        self._running = False

        async def _shutdown() -> None:
            # Closing the socket makes `async for raw in ws` in _main exit; with
            # _running False, _main returns and the loop drains naturally — no
            # tasks are left pending, so no "Event loop is closed" noise.
            if self._ws is not None:
                try:
                    await self._ws.close()
                except Exception:
                    pass

        try:
            fut = asyncio.run_coroutine_threadsafe(_shutdown(), self._loop)
            fut.result(timeout=2.0)
        except Exception:
            pass
        # Let _main finish on its own; only force-stop if it's wedged (e.g. mid
        # reconnect backoff) so close() can't hang.
        if self._thread.is_alive():
            self._thread.join(timeout=3.0)
        if self._thread.is_alive():
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=2.0)

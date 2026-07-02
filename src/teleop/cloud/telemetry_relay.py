"""Operator-feed publisher.

Pushes the *real* robot state, *real* measured network telemetry, and the
supervisor advisories derived from them to operator UIs over the signaling
WebSocket. This is the light status path (small JSON), kept separate from the
heavy WebRTC media path.

There is no synthetic/demo data here: the caller injects ``state_provider`` and
``telemetry_provider`` callables that read the live system (e.g. the leader's
last received follower state and the NetworkMonitor's measured RTT). If a
provider returns ``None`` that field is simply omitted.

Runs as an asyncio task and reconnects automatically.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Callable, Optional

from ..core import SystemConfig, NetworkTelemetry, RobotState
from ..agent import Supervisor

log = logging.getLogger(__name__)

StateProvider = Callable[[], Optional[RobotState]]
TelemetryProvider = Callable[[], Optional[NetworkTelemetry]]


class TelemetryRelay:
    def __init__(self, signaling_url: str, session_id: str,
                 state_provider: StateProvider,
                 telemetry_provider: TelemetryProvider,
                 config: Optional[SystemConfig] = None,
                 supervisor: Optional[Supervisor] = None,
                 peer_id: str = "operator-feed", role: str = "viewer",
                 rate_hz: float = 10.0) -> None:
        url = signaling_url.replace("https://", "wss://").replace("http://", "ws://")
        self.url = url.rstrip("/") + f"/ws/{session_id}/{peer_id}"
        self.role = role
        self.cfg = config or SystemConfig.load()
        self.supervisor = supervisor or Supervisor(self.cfg)
        self.state_provider = state_provider
        self.telemetry_provider = telemetry_provider
        self.period = 1.0 / rate_hz
        self._stop = False

    async def run(self) -> None:
        import websockets
        backoff = 1.0
        while not self._stop:
            try:
                async with websockets.connect(self.url) as ws:
                    await ws.send(json.dumps({"type": "join", "role": self.role}))
                    log.info("operator feed connected: %s", self.url)
                    backoff = 1.0
                    while not self._stop:
                        await self._broadcast(ws)
                        await asyncio.sleep(self.period)
            except Exception:
                if self._stop:
                    break
                log.warning("operator feed reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)

    async def _broadcast(self, ws) -> None:
        state = self.state_provider()
        tele = self.telemetry_provider()
        advisories = self.supervisor.supervise(state, tele)
        if state is not None:
            await ws.send(json.dumps({"type": "state", "state": state.to_dict()}))
        if tele is not None:
            await ws.send(json.dumps({"type": "telemetry", "telemetry": tele.to_dict()}))
        await ws.send(json.dumps({
            "type": "advisory",
            "advisories": [
                {"severity": a.severity.name, "category": a.category, "message": a.message}
                for a in advisories
            ],
        }))

    def stop(self) -> None:
        self._stop = True

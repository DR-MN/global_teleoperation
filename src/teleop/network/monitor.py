"""Network monitor.

Tracks command round-trip latency, packet loss, and stream status, emitting
``NetworkTelemetry`` the supervisor uses for its network-awareness rules. The
leader stamps outgoing commands; the follower echoes the latest stamp in its
status, so RTT is measurable end-to-end without a synchronized clock dependency
beyond the existing wall-clock stamps.
"""
from __future__ import annotations

import logging
from collections import deque
from typing import Deque, Optional

from ..core.config import SystemConfig
from ..core.types import NetworkTelemetry, now

log = logging.getLogger(__name__)


class NetworkMonitor:
    def __init__(self, config: SystemConfig, window: int = 50) -> None:
        self.cfg = config
        self._latencies: Deque[float] = deque(maxlen=window)
        self._sent = 0
        self._acked = 0
        self._last_ack_time: Optional[float] = None
        self._video_latency_ms = 0.0
        self._streams_ok = True
        self._last_acked_stamp = 0.0

    def on_command_sent(self) -> None:
        self._sent += 1

    def on_feedback(self, command_stamp: float) -> None:
        """Called when feedback echoing ``command_stamp`` returns. Latency is the
        round trip for that command. Duplicate echoes of the same stamp (the
        follower re-publishes its last stamp every status frame) are ignored so
        a paused command stream doesn't inflate the latency reading."""
        if command_stamp <= self._last_acked_stamp:
            self._last_ack_time = now()  # link still alive, just no new command
            return
        self._last_acked_stamp = command_stamp
        rtt_ms = max(0.0, (now() - command_stamp) * 1000.0)
        self._latencies.append(rtt_ms)
        self._acked += 1
        self._last_ack_time = now()

    def report_video_latency(self, ms: float, streams_ok: bool = True) -> None:
        self._video_latency_ms = ms
        self._streams_ok = streams_ok

    def telemetry(self) -> NetworkTelemetry:
        latency = (sum(self._latencies) / len(self._latencies)) if self._latencies else 0.0
        loss = 0.0
        if self._sent > 0:
            loss = max(0.0, 1.0 - self._acked / self._sent)
        connected = True
        if self._last_ack_time is not None:
            connected = (now() - self._last_ack_time) <= self.cfg.network.command_timeout_s
        return NetworkTelemetry(
            stamp=now(),
            command_latency_ms=latency,
            video_latency_ms=self._video_latency_ms,
            packet_loss=loss,
            connected=connected,
            streams_ok=self._streams_ok,
        )

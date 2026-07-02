"""Leader teleoperation node.

Reads leader-arm joint targets from a pluggable source and publishes
sequence-numbered, timestamped commands at the control rate. Subscribes to
follower feedback so the operator UI / supervisor can react to robot state.

The ``source`` callable abstracts where targets come from: a real leader arm
driver, a keyboard/SpaceMouse, a recorded trajectory, or — for the demo — a
procedural generator. This keeps the node identical across input devices and
future VR/Quest teleoperation.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from ..core.config import SystemConfig
from ..core.types import CommandMode, JointCommand, RobotState, now
from ..network.monitor import NetworkMonitor
from ..transport.base import (
    Transport, KEY_LEADER_COMMAND, KEY_FOLLOWER_STATE, KEY_FOLLOWER_STATUS,
)

log = logging.getLogger(__name__)

# A source yields (joint_positions, gripper) for the next command, or None to
# indicate "no new input" (the follower then holds its last target).
TargetSource = Callable[[float], Optional[tuple]]


class LeaderNode:
    def __init__(self, config: SystemConfig, transport: Transport,
                 source: TargetSource,
                 monitor: Optional[NetworkMonitor] = None) -> None:
        self.cfg = config
        self.tx = transport
        self.source = source
        # Real network telemetry: RTT is measured from the follower's echoed
        # command stamp, so no cross-machine clock sync is required.
        self.monitor = monitor if monitor is not None else NetworkMonitor(config)
        self._seq = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_state: Optional[RobotState] = None
        self._t0 = time.time()

    def start(self) -> None:
        self.tx.subscribe(KEY_FOLLOWER_STATE, self._on_state)
        self.tx.subscribe(KEY_FOLLOWER_STATUS, self._on_status)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="leader", daemon=True)
        self._thread.start()
        log.info("Leader node started @ %.0f Hz", self.cfg.control_hz)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    @property
    def last_follower_state(self) -> Optional[RobotState]:
        return self._last_state

    def _on_state(self, payload: Dict[str, Any]) -> None:
        try:
            self._last_state = RobotState.from_dict(payload)
        except Exception:
            log.exception("malformed follower state")

    def _on_status(self, payload: Dict[str, Any]) -> None:
        # Use the echoed command stamp to measure real round-trip latency.
        stamp = payload.get("last_cmd_stamp")
        if stamp:
            self.monitor.on_feedback(stamp)

    def _loop(self) -> None:
        period = 1.0 / self.cfg.control_hz
        while self._running:
            t0 = time.time()
            target = self.source(t0 - self._t0)
            if target is not None:
                positions, gripper = target
                self._publish(list(positions), gripper)
            time.sleep(max(0.0, period - (time.time() - t0)))

    def _publish(self, positions: List[float], gripper: float) -> None:
        self._seq += 1
        cmd = JointCommand(
            seq=self._seq,
            stamp=now(),
            mode=CommandMode.JOINT,
            positions=positions,
            gripper=gripper,
        )
        self.monitor.on_command_sent()
        self.tx.publish(KEY_LEADER_COMMAND, cmd.to_dict())

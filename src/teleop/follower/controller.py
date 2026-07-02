"""Follower robot controller.

Receives leader commands over the transport, passes each through the independent
safety controller, drives the arm (sim or real), and publishes robot state +
diagnostics back to the leader at the control rate.

Failure recovery (spec): on comms loss the watchdog stops motion, holds the
current pose, disables command execution, and waits for reconnection — resuming
only after a fresh valid command is validated.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Optional

from ..core.config import SystemConfig
from ..core.types import JointCommand, now
from ..sim.mock_arm import MockArm
from ..transport.base import (
    Transport,
    KEY_LEADER_COMMAND,
    KEY_FOLLOWER_STATE,
    KEY_FOLLOWER_STATUS,
    KEY_FOLLOWER_DIAG,
)
from .safety import SafetyController, Verdict

log = logging.getLogger(__name__)


class FollowerController:
    def __init__(self, config: SystemConfig, transport: Transport,
                 arm: Optional[MockArm] = None) -> None:
        self.cfg = config
        self.tx = transport
        self.arm = arm or MockArm(config.dof, config.joint_limits.max_velocity)
        self.safety = SafetyController(config)
        self._latest: Optional[JointCommand] = None
        self._last_seq = -1
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._holding = False
        self._last_cmd_stamp = 0.0   # leader-clock stamp of last command (for RTT)
        self._stats = {"received": 0, "executed": 0, "rejected": 0,
                       "clamped": 0, "dropped": 0, "estops": 0}

    # ---- lifecycle --------------------------------------------------------
    def start(self) -> None:
        self.tx.subscribe(KEY_LEADER_COMMAND, self._on_command)
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="follower-ctl", daemon=True)
        self._thread.start()
        log.info("Follower controller started @ %.0f Hz", self.cfg.control_hz)

    def stop(self) -> None:
        self._running = False
        if self._thread:
            self._thread.join(timeout=1.0)

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    # ---- command intake ---------------------------------------------------
    def _on_command(self, payload: Dict[str, Any]) -> None:
        try:
            cmd = JointCommand.from_dict(payload)
        except Exception:
            log.exception("malformed command payload")
            return
        with self._lock:
            self._stats["received"] += 1
            # Detect drops/reordering via the sequence number.
            if cmd.seq <= self._last_seq:
                self._stats["dropped"] += 1
                return
            if self._last_seq >= 0 and cmd.seq > self._last_seq + 1:
                self._stats["dropped"] += cmd.seq - self._last_seq - 1
            self._last_seq = cmd.seq
            self._latest = cmd
            self._last_cmd_stamp = cmd.stamp
            self.safety.note_command_received()
            if self._holding:
                log.info("Command flow resumed; exiting safe-state hold")
                self._holding = False

    # ---- control loop -----------------------------------------------------
    def _loop(self) -> None:
        period = 1.0 / self.cfg.control_hz
        while self._running:
            t0 = time.time()
            self._tick()
            self._publish_feedback()
            time.sleep(max(0.0, period - (time.time() - t0)))

    def _tick(self) -> None:
        # Watchdog: comms loss -> hold position, disable execution.
        if self.safety.comms_lost() and not self._holding:
            log.warning("Watchdog: command timeout — entering safe-state hold")
            self.arm.hold()
            self._holding = True

        if self._holding or self.safety.estopped:
            self.arm.step(1.0 / self.cfg.control_hz)
            return

        with self._lock:
            cmd = self._latest
        if cmd is None:
            self.arm.step(1.0 / self.cfg.control_hz)
            return

        result = self.safety.validate(cmd)
        if result.verdict == Verdict.ESTOP:
            self._stats["estops"] += 1
            self.arm.estop()
        elif result.verdict == Verdict.REJECT:
            self._stats["rejected"] += 1
            if result.reasons:
                log.warning("command rejected: %s", "; ".join(result.reasons))
        else:
            if result.verdict == Verdict.CLAMP:
                self._stats["clamped"] += 1
            assert result.command is not None
            self.arm.apply_command(result.command.positions, result.command.gripper)
            self._stats["executed"] += 1
        self.arm.step(1.0 / self.cfg.control_hz)

    def _publish_feedback(self) -> None:
        state = self.arm.read_state()
        self.tx.publish(KEY_FOLLOWER_STATE, state.to_dict())
        self.tx.publish(KEY_FOLLOWER_STATUS, {
            "stamp": now(),
            "status": state.status.value,
            "estopped": self.safety.estopped,
            "holding": self._holding,
            # Echo the last command's (leader-clock) stamp so the leader can
            # measure real round-trip latency without cross-machine clock sync.
            "last_cmd_stamp": self._last_cmd_stamp,
        })
        self.tx.publish(KEY_FOLLOWER_DIAG, {"stamp": now(), **self._stats})

"""Independent safety controller.

Per the spec, the local safety controller runs independently of the
teleoperation link and is the final authority before motion. Every command is
validated; unsafe commands are rejected or clamped, and the watchdog forces a
safe-state hold when commands go stale (comms loss).

Priority order (from ctx.txt): protect humans > protect hardware > stable
teleop > task completion. This module only ever makes motion *more*
conservative — it never expands limits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..core.config import SystemConfig
from ..core.types import JointCommand, Pose, now
from ..sim.mock_arm import forward_kinematics

log = logging.getLogger(__name__)


class Verdict(str, Enum):
    ACCEPT = "accept"          # safe as-is
    CLAMP = "clamp"            # safe after clamping to soft limits
    REJECT = "reject"          # unsafe; do not execute
    ESTOP = "estop"            # hard-limit/critical breach; latch E-stop


@dataclass
class SafetyResult:
    verdict: Verdict
    command: Optional[JointCommand]   # possibly clamped; None if rejected
    reasons: List[str] = field(default_factory=list)

    @property
    def safe(self) -> bool:
        return self.verdict in (Verdict.ACCEPT, Verdict.CLAMP)


class SafetyController:
    def __init__(self, config: SystemConfig) -> None:
        self.cfg = config
        self.jl = config.joint_limits
        self.ws = config.workspace
        self._estopped = False
        self._last_command_time: Optional[float] = None

    # ---- E-stop state -----------------------------------------------------
    @property
    def estopped(self) -> bool:
        return self._estopped

    def trigger_estop(self, reason: str = "manual") -> None:
        if not self._estopped:
            log.error("E-STOP latched: %s", reason)
        self._estopped = True

    def reset_estop(self) -> None:
        log.warning("E-stop reset requested")
        self._estopped = False

    # ---- Watchdog ---------------------------------------------------------
    def note_command_received(self) -> None:
        self._last_command_time = now()

    def comms_lost(self) -> bool:
        """True if no command has arrived within the command timeout."""
        if self._last_command_time is None:
            return False
        return (now() - self._last_command_time) > self.cfg.network.command_timeout_s

    # ---- Command validation ----------------------------------------------
    def validate(self, cmd: JointCommand) -> SafetyResult:
        if self._estopped:
            return SafetyResult(Verdict.REJECT, None, ["E-stop latched"])

        reasons: List[str] = []
        positions = list(cmd.positions[: self.cfg.dof])

        if len(positions) != self.cfg.dof:
            return SafetyResult(Verdict.REJECT, None,
                                [f"expected {self.cfg.dof} joints, got {len(positions)}"])
        if any(not _finite(p) for p in positions):
            self.trigger_estop("non-finite joint target")
            return SafetyResult(Verdict.ESTOP, None, ["non-finite joint value"])

        # Hard joint limits -> E-stop (cannot be made safe by clamping).
        for i, p in enumerate(positions):
            if p < self.jl.hard_lower[i] or p > self.jl.hard_upper[i]:
                self.trigger_estop(f"joint {i} hard-limit breach ({p:.3f})")
                return SafetyResult(Verdict.ESTOP, None,
                                    [f"joint {i} beyond hard limit"])

        # Velocity check against commanded velocities.
        for i, v in enumerate(cmd.velocities[: self.cfg.dof]):
            if abs(v) > self.jl.max_velocity[i]:
                reasons.append(f"joint {i} velocity {v:.2f} > max; clamped")

        # Soft joint limits -> clamp.
        clamped = False
        for i, p in enumerate(positions):
            lo, hi = self.jl.soft_lower[i], self.jl.soft_upper[i]
            if p < lo or p > hi:
                positions[i] = max(lo, min(hi, p))
                clamped = True
                reasons.append(f"joint {i} clamped to soft limit")

        # Workspace check on the resulting EE position.
        pose = cmd.pose or forward_kinematics(positions)
        if not self._in_workspace(pose):
            reasons.append("EE target outside workspace; rejected")
            return SafetyResult(Verdict.REJECT, None, reasons)

        out = JointCommand(
            seq=cmd.seq,
            stamp=cmd.stamp,
            mode=cmd.mode,
            positions=positions,
            velocities=list(cmd.velocities[: self.cfg.dof]),
            pose=cmd.pose,
            gripper=max(0.0, min(1.0, cmd.gripper)),
        )
        verdict = Verdict.CLAMP if clamped else Verdict.ACCEPT
        return SafetyResult(verdict, out, reasons)

    def _in_workspace(self, pose: Pose) -> bool:
        return (
            self.ws.x_min <= pose.x <= self.ws.x_max
            and self.ws.y_min <= pose.y <= self.ws.y_max
            and self.ws.z_min <= pose.z <= self.ws.z_max
        )


def _finite(v: float) -> bool:
    return v == v and v not in (float("inf"), float("-inf"))

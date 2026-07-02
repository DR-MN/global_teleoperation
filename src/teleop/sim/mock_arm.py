"""Kinematic mock of a 6DOF arm + parallel gripper.

This is intentionally simple: each joint is a first-order actuator that tracks a
commanded position at a bounded velocity. It exposes the exact same surface a
MuJoCo/Isaac/real-hardware driver would (``apply_command`` / ``step`` /
``read_state``), so the follower controller is agnostic to what's behind it.

A lightweight forward-kinematics approximation maps joint angles to an
end-effector position so workspace limits and EE pose feedback are meaningful
without pulling in a full URDF/physics dependency.
"""
from __future__ import annotations

import math
from typing import List

from ..core.types import (
    DOF,
    Pose,
    RobotState,
    RobotStatus,
    GripperState,
    now,
)

# Simple 6DOF link lengths (meters) for the planar FK approximation.
_LINK = [0.0, 0.20, 0.20, 0.10, 0.05, 0.05]


def forward_kinematics(positions: List[float]) -> Pose:
    """Approximate FK: treat the arm as a vertical shoulder + planar elbow chain.

    Good enough for workspace-limit checking and plausible EE feedback in the
    hardware-free slice; swap for a URDF/pinocchio solver when wiring real HW.
    """
    base = positions[0]
    reach = (
        _LINK[1] * math.cos(positions[1])
        + _LINK[2] * math.cos(positions[1] + positions[2])
        + _LINK[3] * math.cos(positions[1] + positions[2] + positions[3])
    )
    height = (
        0.10
        + _LINK[1] * math.sin(positions[1])
        + _LINK[2] * math.sin(positions[1] + positions[2])
        + _LINK[3] * math.sin(positions[1] + positions[2] + positions[3])
    )
    x = reach * math.cos(base)
    y = reach * math.sin(base)
    z = height
    # Orientation: fold remaining wrist joints into a yaw quaternion.
    yaw = positions[4] + positions[5]
    return Pose(x=x, y=y, z=z, qz=math.sin(yaw / 2), qw=math.cos(yaw / 2))


class MockArm:
    def __init__(self, dof: int = DOF, max_velocity: List[float] | None = None) -> None:
        self.dof = dof
        self._pos = [0.0] * dof
        self._vel = [0.0] * dof
        self._target = [0.0] * dof
        self._gripper = 0.0          # 0 open … 1 closed
        self._gripper_target = 0.0
        self._max_vel = max_velocity or [3.0] * dof
        self._status = RobotStatus.READY
        self._seq = 0

    def apply_command(self, positions: List[float], gripper: float) -> None:
        self._target = list(positions[: self.dof])
        self._gripper_target = max(0.0, min(1.0, gripper))

    def hold(self) -> None:
        """Freeze at the current pose (safe-state on comms loss)."""
        self._target = list(self._pos)
        self._vel = [0.0] * self.dof
        self._status = RobotStatus.HOLDING

    def estop(self) -> None:
        self.hold()
        self._status = RobotStatus.ESTOP

    def step(self, dt: float) -> None:
        if self._status == RobotStatus.ESTOP:
            return
        moving = False
        for i in range(self.dof):
            err = self._target[i] - self._pos[i]
            step = max(-self._max_vel[i] * dt, min(self._max_vel[i] * dt, err))
            self._pos[i] += step
            self._vel[i] = step / dt if dt > 0 else 0.0
            if abs(err) > 1e-4:
                moving = True
        g_err = self._gripper_target - self._gripper
        self._gripper += max(-2.0 * dt, min(2.0 * dt, g_err))
        if self._status != RobotStatus.HOLDING:
            self._status = RobotStatus.MOVING if moving else RobotStatus.READY

    def read_state(self) -> RobotState:
        self._seq += 1
        if self._gripper < 0.1:
            gs = GripperState.OPEN
        elif self._gripper > 0.9:
            gs = GripperState.CLOSED
        else:
            gs = GripperState.MOVING
        return RobotState(
            seq=self._seq,
            stamp=now(),
            positions=list(self._pos),
            velocities=list(self._vel),
            pose=forward_kinematics(self._pos),
            gripper_state=gs,
            gripper_position=self._gripper,
            status=self._status,
        )

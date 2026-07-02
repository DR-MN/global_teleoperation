"""Real SO-101 follower arm driver.

Drop-in replacement for ``MockArm`` that drives a physical SO-101 (Feetech
STS3215 bus) through the vendored ``so101_ros2.lerobot.so101.SO101`` device
class. It exposes the exact same surface the ``FollowerController`` expects
(``apply_command`` / ``step`` / ``read_state`` / ``hold`` / ``estop``), so every
command reaching the hardware has already passed through the safety controller
and the leader receives *real* follower feedback.

Units: the controller works in radians; the SO-101 bus works in the same
normalized "degree" units the vendored ``*_with_conversion`` nodes use, so this
driver converts rad<->deg at the boundary exactly as those nodes do.

The vendored device class (and its Feetech SDK deps) is imported lazily so the
core ``teleop`` library stays hardware-free unless an SO-101 is actually used.
"""
from __future__ import annotations

import math
from typing import List, Optional

from ..core.types import (
    DOF,
    RobotState,
    RobotStatus,
    GripperState,
    now,
)
from ..sim.mock_arm import forward_kinematics

# Joint order shared with the vendored SO-101 nodes. positions[5] is the gripper.
JOINT_NAMES = [
    "shoulder_pan",
    "shoulder_lift",
    "elbow_flex",
    "wrist_flex",
    "wrist_roll",
    "gripper",
]

# The gripper motor reports a normalized 0..100 value (open..closed); RobotState
# carries gripper_position as 0.0..1.0, so we scale by this.
_GRIPPER_RANGE = 100.0


class SO101Arm:
    """Hardware follower arm. Same surface as ``MockArm``."""

    def __init__(
        self,
        dof: int = DOF,
        max_velocity: Optional[List[float]] = None,
        port: str = "/dev/ttyACM0",
        calibration_dir: Optional[str] = None,
        robot_name: str = "so101_follower",
    ) -> None:
        if dof != len(JOINT_NAMES):
            raise ValueError(
                f"SO101Arm is fixed at {len(JOINT_NAMES)} DOF; got dof={dof}"
            )
        self.dof = dof
        # Lazy import: only pull in the Feetech SDK when a real arm is built.
        from so101_ros2.lerobot.so101 import SO101

        self._robot = SO101(port=port, name=robot_name, calibration_dir=calibration_dir)
        self._robot.connect()

        self._status = RobotStatus.READY
        self._seq = 0
        # Velocity is estimated by finite-differencing successive reads.
        self._last_pos: Optional[List[float]] = None
        self._last_read_t: Optional[float] = None
        self._last_vel = [0.0] * dof

    # ---- command path -----------------------------------------------------
    def apply_command(self, positions: List[float], gripper: float) -> None:
        """Write a (already safety-validated) joint command to the hardware.

        ``positions`` are radians in JOINT_NAMES order; ``positions[5]`` is the
        gripper. The separate ``gripper`` arg is unused for SO-101 (the gripper
        rides in the position vector, matching the leader publisher)."""
        goal_deg = {
            name: math.degrees(float(pos))
            for name, pos in zip(JOINT_NAMES, positions[: self.dof])
        }
        self._robot._bus.sync_write("Goal_Position", goal_deg)
        if self._status not in (RobotStatus.ESTOP, RobotStatus.HOLDING):
            self._status = RobotStatus.MOVING

    def hold(self) -> None:
        """Freeze at the current pose (safe-state on comms loss)."""
        self._write_current_as_goal()
        self._status = RobotStatus.HOLDING

    def estop(self) -> None:
        self._write_current_as_goal()
        self._status = RobotStatus.ESTOP

    def _write_current_as_goal(self) -> None:
        try:
            state = self._robot.get_device_state()  # {name: deg}
            self._robot._bus.sync_write("Goal_Position", state)
        except Exception:
            # Best-effort hold; never raise out of a safety transition.
            pass

    def step(self, dt: float) -> None:
        """No-op: the servos close their own position loop in hardware.

        State (and velocity estimate) is refreshed in ``read_state``."""
        return

    # ---- feedback path ----------------------------------------------------
    def read_state(self) -> RobotState:
        self._seq += 1
        state = self._robot.get_device_state()  # {name: deg} in motor order
        positions = [math.radians(float(state[name])) for name in JOINT_NAMES]

        t = now()
        if self._last_pos is not None and self._last_read_t is not None:
            dt = t - self._last_read_t
            if dt > 0:
                self._last_vel = [
                    (positions[i] - self._last_pos[i]) / dt for i in range(self.dof)
                ]
        self._last_pos = positions
        self._last_read_t = t

        gripper_pos = max(0.0, min(1.0, float(state["gripper"]) / _GRIPPER_RANGE))
        if gripper_pos < 0.1:
            gs = GripperState.OPEN
        elif gripper_pos > 0.9:
            gs = GripperState.CLOSED
        else:
            gs = GripperState.MOVING

        # Promote MOVING->READY once motion settles (when not holding/estopped).
        if self._status == RobotStatus.MOVING and max(
            (abs(v) for v in self._last_vel), default=0.0
        ) < 1e-3:
            self._status = RobotStatus.READY

        return RobotState(
            seq=self._seq,
            stamp=t,
            positions=positions,
            velocities=list(self._last_vel),
            pose=forward_kinematics(positions),
            gripper_state=gs,
            gripper_position=gripper_pos,
            status=self._status,
        )

    # ---- lifecycle --------------------------------------------------------
    def disconnect(self) -> None:
        try:
            if self._robot.is_connected:
                self._robot.disconnect()
        except Exception:
            pass

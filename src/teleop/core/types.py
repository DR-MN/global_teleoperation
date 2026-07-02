"""Shared data types for the teleoperation system.

These dataclasses are the contract between every component (leader, transport,
follower, safety, recorder, agent). They are transport-agnostic: each has a
``to_dict`` / ``from_dict`` so they can be serialized over Zenoh, ROS2, JSON or
an in-process queue without change.

The arm is a generic N-DOF arm (default 6) so that the same types extend to
dual-arm and humanoid platforms without modification, per the spec's future
compatibility requirements.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

# Default kinematic dimensionality. A single 6DOF arm today; raise/extend for
# dual-arm (12) or humanoid platforms without touching downstream code.
DOF = 6


def now() -> float:
    """Monotonic wall-clock timestamp in seconds (UTC epoch)."""
    return time.time()


class RobotStatus(str, Enum):
    IDLE = "idle"
    READY = "ready"
    MOVING = "moving"
    HOLDING = "holding"      # safe-state hold (e.g. comms lost)
    ESTOP = "estop"          # emergency stop latched
    FAULT = "fault"          # hardware/controller fault


class GripperState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    MOVING = "moving"
    UNKNOWN = "unknown"


class CommandMode(str, Enum):
    JOINT = "joint"          # joint-space mirroring
    CARTESIAN = "cartesian"  # end-effector pose mirroring


@dataclass
class Pose:
    """End-effector pose: position (m) + quaternion orientation (x, y, z, w)."""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    qx: float = 0.0
    qy: float = 0.0
    qz: float = 0.0
    qw: float = 1.0

    def position(self) -> List[float]:
        return [self.x, self.y, self.z]

    def to_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Pose":
        return cls(**{k: d[k] for k in cls().__dict__ if k in d})


@dataclass
class JointCommand:
    """A command sent leader → follower. Sequence + timestamp let the follower
    detect drops/reordering and let the safety watchdog measure staleness."""
    seq: int
    stamp: float
    mode: CommandMode = CommandMode.JOINT
    positions: List[float] = field(default_factory=lambda: [0.0] * DOF)
    velocities: List[float] = field(default_factory=lambda: [0.0] * DOF)
    pose: Optional[Pose] = None
    gripper: float = 0.0  # 0.0 = open, 1.0 = fully closed

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["mode"] = self.mode.value
        d["pose"] = self.pose.to_dict() if self.pose else None
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "JointCommand":
        return cls(
            seq=d["seq"],
            stamp=d["stamp"],
            mode=CommandMode(d.get("mode", "joint")),
            positions=list(d.get("positions", [0.0] * DOF)),
            velocities=list(d.get("velocities", [0.0] * DOF)),
            pose=Pose.from_dict(d["pose"]) if d.get("pose") else None,
            gripper=d.get("gripper", 0.0),
        )


@dataclass
class RobotState:
    """Follower → leader feedback: the full observable state of the arm."""
    seq: int
    stamp: float
    positions: List[float] = field(default_factory=lambda: [0.0] * DOF)
    velocities: List[float] = field(default_factory=lambda: [0.0] * DOF)
    pose: Pose = field(default_factory=Pose)
    gripper_state: GripperState = GripperState.UNKNOWN
    gripper_position: float = 0.0
    status: RobotStatus = RobotStatus.IDLE
    error_codes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["pose"] = self.pose.to_dict()
        d["gripper_state"] = self.gripper_state.value
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "RobotState":
        return cls(
            seq=d["seq"],
            stamp=d["stamp"],
            positions=list(d.get("positions", [0.0] * DOF)),
            velocities=list(d.get("velocities", [0.0] * DOF)),
            pose=Pose.from_dict(d.get("pose", {})),
            gripper_state=GripperState(d.get("gripper_state", "unknown")),
            gripper_position=d.get("gripper_position", 0.0),
            status=RobotStatus(d.get("status", "idle")),
            error_codes=list(d.get("error_codes", [])),
        )


@dataclass
class NetworkTelemetry:
    """Link health between leader and follower, sampled by the network monitor."""
    stamp: float
    command_latency_ms: float = 0.0
    video_latency_ms: float = 0.0
    packet_loss: float = 0.0          # fraction 0.0–1.0
    connected: bool = True
    streams_ok: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "NetworkTelemetry":
        return cls(**{k: d[k] for k in cls(stamp=0.0).__dict__ if k in d})

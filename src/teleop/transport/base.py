"""Transport abstraction.

The control plane (commands + feedback) is decoupled from the wire protocol so
the same leader/follower code runs over an in-process queue (tests, sim), Zenoh
(production, per spec), or a future ROS2 bridge. A transport carries opaque
JSON-serializable dicts on named "keys" (topics). Components pub/sub by key.
"""
from __future__ import annotations

import abc
from typing import Any, Callable, Dict

# Canonical topic/key names, mirroring the ROS2 topics in ctx.txt.
KEY_LEADER_COMMAND = "leader/command"          # JointCommand
KEY_FOLLOWER_STATE = "follower/joint_states"   # RobotState
KEY_FOLLOWER_STATUS = "follower/status"        # status dict
KEY_FOLLOWER_DIAG = "follower/diagnostics"     # diagnostics dict

Handler = Callable[[Dict[str, Any]], None]


class Transport(abc.ABC):
    """Minimal pub/sub interface. Implementations must be safe to call from the
    control thread and must never raise on publish (drop + log instead) so a
    transient network fault cannot crash the control loop."""

    @abc.abstractmethod
    def publish(self, key: str, payload: Dict[str, Any]) -> None:
        ...

    @abc.abstractmethod
    def subscribe(self, key: str, handler: Handler) -> None:
        ...

    @abc.abstractmethod
    def close(self) -> None:
        ...

    def __enter__(self) -> "Transport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

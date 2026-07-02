"""Pluggable control-plane transport (in-process or Zenoh)."""
from .base import (
    Transport,
    Handler,
    KEY_LEADER_COMMAND,
    KEY_FOLLOWER_STATE,
    KEY_FOLLOWER_STATUS,
    KEY_FOLLOWER_DIAG,
)
from .inproc import InProcTransport
from .zenoh_transport import ZenohTransport, make_transport

__all__ = [
    "Transport",
    "Handler",
    "KEY_LEADER_COMMAND",
    "KEY_FOLLOWER_STATE",
    "KEY_FOLLOWER_STATUS",
    "KEY_FOLLOWER_DIAG",
    "InProcTransport",
    "ZenohTransport",
    "make_transport",
]

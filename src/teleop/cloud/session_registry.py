"""Session registry for the cloud layer.

Tracks teleoperation sessions and the peers (leader / follower) connected to
each. The registry is the rendezvous point so a leader and follower that have
never seen each other's IPs can find each other by ``session_id`` and exchange
WebRTC signaling. Pure in-memory and dependency-free so it is unit-testable
without a running server.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Dict, List, Optional


class Role(str, Enum):
    LEADER = "leader"
    FOLLOWER = "follower"
    VIEWER = "viewer"   # video-only observer


@dataclass
class Peer:
    peer_id: str
    role: Role
    joined: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)


@dataclass
class Session:
    session_id: str
    created: float = field(default_factory=time.time)
    peers: Dict[str, Peer] = field(default_factory=dict)

    def role_count(self, role: Role) -> int:
        return sum(1 for p in self.peers.values() if p.role == role)


class SessionRegistry:
    def __init__(self, peer_ttl_s: float = 30.0) -> None:
        self._sessions: Dict[str, Session] = {}
        self._ttl = peer_ttl_s
        self._lock = RLock()

    def join(self, session_id: str, peer_id: str, role: Role) -> Session:
        with self._lock:
            sess = self._sessions.setdefault(session_id, Session(session_id))
            sess.peers[peer_id] = Peer(peer_id=peer_id, role=role)
            return sess

    def leave(self, session_id: str, peer_id: str) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return
            sess.peers.pop(peer_id, None)
            if not sess.peers:
                self._sessions.pop(session_id, None)

    def heartbeat(self, session_id: str, peer_id: str) -> None:
        with self._lock:
            sess = self._sessions.get(session_id)
            if sess and peer_id in sess.peers:
                sess.peers[peer_id].last_seen = time.time()

    def peers(self, session_id: str, exclude: Optional[str] = None) -> List[Peer]:
        with self._lock:
            sess = self._sessions.get(session_id)
            if not sess:
                return []
            return [p for pid, p in sess.peers.items() if pid != exclude]

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_sessions(self) -> List[Session]:
        with self._lock:
            return list(self._sessions.values())

    def prune(self) -> int:
        """Drop peers (and empty sessions) past the heartbeat TTL. Returns the
        number of peers removed. Call periodically to recover from peers that
        vanished without a clean leave (network interruption)."""
        removed = 0
        cutoff = time.time() - self._ttl
        with self._lock:
            for sid in list(self._sessions):
                sess = self._sessions[sid]
                for pid in [p for p, peer in sess.peers.items() if peer.last_seen < cutoff]:
                    sess.peers.pop(pid, None)
                    removed += 1
                if not sess.peers:
                    self._sessions.pop(sid, None)
        return removed

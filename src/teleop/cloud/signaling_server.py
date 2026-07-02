"""WebRTC signaling server (FastAPI + WebSocket).

Responsibilities (Cloud Layer in the spec):
* Session registry / rendezvous by ``session_id``.
* Relay WebRTC signaling (SDP offer/answer + ICE candidates) between peers that
  are on different networks behind NAT.
* Serve ICE server config (STUN/TURN) so peers can negotiate NAT traversal.
* Health/observability endpoints.

The signaling channel carries only small JSON control messages — never media.
Media flows peer-to-peer over WebRTC (with TURN relay fallback).

Run:
    uvicorn teleop.cloud.signaling_server:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List

from .session_registry import SessionRegistry, Role

log = logging.getLogger(__name__)

try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
except Exception as e:  # pragma: no cover - optional dependency
    raise RuntimeError(
        "FastAPI is required for the signaling server. "
        "Install with: pip install -e '.[api]'"
    ) from e


def ice_servers() -> List[Dict[str, object]]:
    """ICE servers from env (no hardcoded infra). STUN only discovers your public
    IP; TURN actually *relays* the media and is required when the two peers are
    on different networks behind strict NAT.

    Three ways to configure, most flexible first:
      * ICE_SERVERS — a JSON array of RTCIceServer dicts (paste a provider's
        block verbatim, e.g. Metered/Twilio). Overrides everything else.
      * TURN_URL[S] (comma-separated) + TURN_USERNAME + TURN_CREDENTIAL.
      * STUN_URL (default Google STUN) — fallback, no relay.
    """
    raw = os.environ.get("ICE_SERVERS")
    if raw:
        try:
            servers = json.loads(raw)
            if isinstance(servers, list) and servers:
                return servers
            log.warning("ICE_SERVERS is not a non-empty JSON array; ignoring")
        except json.JSONDecodeError:
            log.warning("ICE_SERVERS is not valid JSON; ignoring")

    servers: List[Dict[str, object]] = [
        {"urls": os.environ.get("STUN_URL", "stun:stun.l.google.com:19302")}
    ]
    turn_urls = os.environ.get("TURN_URLS") or os.environ.get("TURN_URL")
    if turn_urls:
        urls = [u.strip() for u in turn_urls.split(",") if u.strip()]
        servers.append({
            "urls": urls if len(urls) > 1 else urls[0],
            "username": os.environ.get("TURN_USERNAME", ""),
            "credential": os.environ.get("TURN_CREDENTIAL", ""),
        })
    return servers


registry = SessionRegistry()
app = FastAPI(title="Teleop Signaling Server", version="0.1.0")


@app.get("/health")
def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "sessions": len(registry.list_sessions())})


@app.get("/ice")
def ice() -> JSONResponse:
    return JSONResponse({"iceServers": ice_servers()})


@app.get("/sessions")
def sessions() -> JSONResponse:
    registry.prune()
    return JSONResponse({
        "sessions": [
            {
                "session_id": s.session_id,
                "created": s.created,
                "peers": [
                    {"peer_id": p.peer_id, "role": p.role.value, "last_seen": p.last_seen}
                    for p in s.peers.values()
                ],
            }
            for s in registry.list_sessions()
        ]
    })


class ConnectionHub:
    """Maps peer_id -> live WebSocket so signaling can be routed by recipient."""

    def __init__(self) -> None:
        self._ws: Dict[str, WebSocket] = {}
        self._session: Dict[str, str] = {}

    def add(self, peer_id: str, ws: WebSocket, session_id: str = "") -> None:
        self._ws[peer_id] = ws
        self._session[peer_id] = session_id

    def remove(self, peer_id: str) -> None:
        self._ws.pop(peer_id, None)
        self._session.pop(peer_id, None)

    async def send(self, peer_id: str, message: dict) -> bool:
        ws = self._ws.get(peer_id)
        if ws is None:
            return False
        try:
            await ws.send_text(json.dumps(message))
            return True
        except Exception:
            self._drop(peer_id)
            return False

    async def send_bytes(self, peer_id: str, data: bytes) -> bool:
        """Relay a binary video frame to one peer (websocket video transport)."""
        ws = self._ws.get(peer_id)
        if ws is None:
            return False
        try:
            await ws.send_bytes(data)
            return True
        except Exception:
            self._drop(peer_id)
            return False

    def _drop(self, peer_id: str) -> None:
        # The recipient's socket is gone (browser closed / reconnected). Drop it
        # from the hub and registry so high-rate broadcasters stop retrying a
        # dead connection — one quiet line, not a traceback.
        session_id = self._session.get(peer_id, "")
        log.info("peer %s unreachable; dropping", peer_id)
        self.remove(peer_id)
        if session_id:
            registry.leave(session_id, peer_id)


hub = ConnectionHub()

# Per-peer video metadata advertised on join, e.g. {"transport": "websocket",
# "format": "base64"}. Relayed to viewers so they know whether to negotiate
# WebRTC or render pushed JPEG frames. Kept out of the registry to avoid
# coupling rendezvous state to the video transport.
peer_video: Dict[str, dict] = {}


def _peer_info(peer) -> dict:
    """Peer descriptor sent to other peers, including any advertised video mode."""
    info = {"peer_id": peer.peer_id, "role": peer.role.value}
    vid = peer_video.get(peer.peer_id)
    if vid:
        info["video"] = vid
    return info


@app.websocket("/ws/{session_id}/{peer_id}")
async def signaling(ws: WebSocket, session_id: str, peer_id: str) -> None:
    """Signaling endpoint.

    Inbound message shape (JSON):
        {"type": "join", "role": "leader|follower|viewer"}
        {"type": "offer"|"answer"|"candidate", "to": "<peer_id>", ...}
        {"type": "heartbeat"}
    Messages with a ``to`` field are relayed to that peer; a missing ``to`` is
    broadcast to all other peers in the session.
    """
    await ws.accept()
    hub.add(peer_id, ws, session_id)
    role = Role.VIEWER
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break

            # Binary frame = websocket video (1 byte cam id + JPEG). Broadcast the
            # raw bytes to every other peer in the session; never touches JSON.
            data = message.get("bytes")
            if data is not None:
                for p in registry.peers(session_id, exclude=peer_id):
                    await hub.send_bytes(p.peer_id, data)
                continue

            raw = message.get("text")
            if raw is None:
                continue
            msg = json.loads(raw)
            mtype = msg.get("type")

            if mtype == "join":
                try:
                    role = Role(msg.get("role", "viewer"))
                except ValueError:
                    role = Role.VIEWER  # tolerate control-plane peers
                registry.join(session_id, peer_id, role)
                video = msg.get("video")
                if isinstance(video, dict):
                    peer_video[peer_id] = video
                peers = registry.peers(session_id, exclude=peer_id)
                await ws.send_text(json.dumps({
                    "type": "joined",
                    "session_id": session_id,
                    "peer_id": peer_id,
                    "iceServers": ice_servers(),
                    "peers": [_peer_info(p) for p in peers],
                }))
                # Notify existing peers a new one arrived so they can offer (or,
                # for a websocket-video follower, start rendering pushed frames).
                joined_msg = {"type": "peer-joined", "peer_id": peer_id, "role": role.value}
                if peer_id in peer_video:
                    joined_msg["video"] = peer_video[peer_id]
                for p in peers:
                    await hub.send(p.peer_id, joined_msg)

            elif mtype == "heartbeat":
                registry.heartbeat(session_id, peer_id)

            elif mtype in ("offer", "answer", "candidate", "telemetry",
                           "advisory", "state", "pub", "video-frame"):
                msg["from"] = peer_id
                target = msg.get("to")
                if target:
                    await hub.send(target, msg)
                else:  # broadcast to the rest of the session
                    for p in registry.peers(session_id, exclude=peer_id):
                        await hub.send(p.peer_id, msg)
            else:
                log.warning("unknown signaling message type: %s", mtype)

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("signaling error for %s/%s", session_id, peer_id)
    finally:
        hub.remove(peer_id)
        peer_video.pop(peer_id, None)
        registry.leave(session_id, peer_id)
        # Tell remaining peers so they can tear down the RTCPeerConnection.
        for p in registry.peers(session_id):
            await hub.send(p.peer_id, {"type": "peer-left", "peer_id": peer_id})

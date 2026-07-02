"""Follower-side WebRTC video publisher.

Publishes the global + wrist camera tracks to viewers (leader UI / browser) via
the cloud signaling server. Implements the spec's video requirements:
* Multiple simultaneous streams (one RTCPeerConnection per viewer, N tracks).
* Automatic reconnection to the signaling server with exponential backoff.
* Graceful teardown on peer-left.

aiortc + PyAV are optional deps (``pip install -e '.[video]'``); this module
imports without them so the rest of the package is unaffected.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, Optional

from .camera import CameraConfig, make_camera

log = logging.getLogger(__name__)


class _IceTeardownFormatter(logging.Filter):
    """Reformat aioice teardown noise into a clear timestamped event so the
    operator can see exactly when a viewer disconnected or the browser refreshed.
    Suppresses the raw aioice stacktrace and replaces it with one clean line."""

    import datetime as _dt

    _BENIGN = ("socket.send() raised exception", "TransactionTimeout")

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        if any(token in msg for token in self._BENIGN):
            ts = self._dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log.info("[%s] socket.send() raised exception — viewer disconnected or browser refreshed", ts)
            return False   # drop the original noisy aioice record
        return True


logging.getLogger("aioice").addFilter(_IceTeardownFormatter())
logging.getLogger("aioice.ice").addFilter(_IceTeardownFormatter())


try:
    import av  # type: ignore
    from aiortc import (  # type: ignore
        RTCPeerConnection, RTCConfiguration, RTCIceServer,
        RTCSessionDescription, VideoStreamTrack,
    )
    import websockets  # type: ignore
    _HAVE_WEBRTC = True
except Exception:  # pragma: no cover - optional deps
    _HAVE_WEBRTC = False


if _HAVE_WEBRTC:

    class CameraTrack(VideoStreamTrack):
        """Wraps a camera source as a WebRTC video track at the configured FPS."""

        def __init__(self, cfg: CameraConfig, cam=None) -> None:
            super().__init__()
            self.cfg = cfg
            self.cam = cam if cam is not None else make_camera(cfg)

        async def recv(self):
            pts, time_base = await self.next_timestamp()
            frame_ndarray = self.cam.read()
            frame = av.VideoFrame.from_ndarray(frame_ndarray, format="bgr24")
            frame.pts = pts
            frame.time_base = time_base
            return frame

        def stop(self) -> None:  # type: ignore[override]
            super().stop()
            self.cam.close()


def _quiet_ice_teardown(loop, context: dict) -> None:
    """Swallow harmless aioice/TURN background noise that doesn't affect a live
    connection, and pass everything else to the default handler.

    Two known-benign cases:
      * STUN retransmit timers firing after a peer closes -> AttributeError on a
        torn-down transport ('NoneType' has no attribute sendto/...).
      * ICE trying every TURN candidate pair in parallel: the *losing* pairs time
        out their channel-bind (TransactionTimeout) while another pair already
        connected. These surface as 'Task exception was never retrieved'.
    """
    exc = context.get("exception")
    text = f"{context.get('message', '')} {type(exc).__name__}: {exc}"
    benign = (
        "sendto", "call_exception_handler",          # post-close transport
        "TransactionTimeout",                          # losing TURN/STUN pair
        "socket.send() raised exception",              # torn-down UDP socket
    )
    if any(token in text for token in benign):
        log.debug("suppressed ICE/TURN teardown noise: %s", text.strip())
        return
    loop.default_exception_handler(context)


class VideoPublisher:
    """Connects to the signaling server as a 'follower' and answers viewer
    offers with the camera tracks. One instance serves all viewers."""

    def __init__(self, signaling_url: str, session_id: str,
                 peer_id: str = "follower-video",
                 global_cfg: Optional[CameraConfig] = None,
                 wrist_cfg: Optional[CameraConfig] = None,
                 global_cam=None,
                 wrist_cam=None) -> None:
        if not _HAVE_WEBRTC:
            raise RuntimeError(
                "WebRTC deps missing. Install with: pip install -e '.[video]' "
                "(aiortc, av, websockets)."
            )
        self.url = signaling_url.rstrip("/") + f"/ws/{session_id}/{peer_id}"
        self.peer_id = peer_id
        self.global_cfg = global_cfg or CameraConfig("global", 1280, 720, 30)
        self.wrist_cfg = wrist_cfg or CameraConfig("wrist", 640, 480, 30)
        self._global_cam = global_cam   # pre-built camera instance (e.g. ROS2Camera)
        self._wrist_cam = wrist_cam
        self._pcs: Dict[str, "RTCPeerConnection"] = {}
        self._ice_servers: list = []
        self._stop = False
        self._relay = None
        self._global_src = None
        self._wrist_src = None

    def _ensure_sources(self) -> None:
        if self._relay is None:
            from aiortc.contrib.media import MediaRelay  # type: ignore
            self._relay = MediaRelay()
            self._global_src = CameraTrack(self.global_cfg, self._global_cam)
            self._wrist_src = CameraTrack(self.wrist_cfg, self._wrist_cam)
            log.info("camera sources ready (global=%s, wrist=%s)",
                     self.global_cfg.name, self.wrist_cfg.name)

    async def run(self) -> None:
        # aioice schedules STUN retransmits that can fire after a peer's
        # transport is torn down, raising a harmless 'NoneType has no attribute
        # sendto' from a timer callback. Swallow exactly that noise.
        try:
            asyncio.get_running_loop().set_exception_handler(_quiet_ice_teardown)
        except RuntimeError:
            pass
        backoff = 1.0
        while not self._stop:
            try:
                await self._session()
                backoff = 1.0  # reset after a clean session
            except Exception:
                log.exception("publisher session error; reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)  # exp. backoff per spec

    async def _session(self) -> None:
        async with websockets.connect(self.url) as ws:
            await ws.send(json.dumps({"type": "join", "role": "follower"}))
            log.info("video publisher joined session at %s", self.url)
            async for raw in ws:
                msg = json.loads(raw)
                try:
                    await self._handle(ws, msg)
                except Exception:
                    # One bad signaling message (e.g. an unparsable ICE
                    # candidate) must never tear down the whole session and
                    # drop a connected viewer.
                    log.exception("error handling %s message; continuing",
                                  msg.get("type"))

    async def _handle(self, ws, msg: dict) -> None:
        mtype = msg.get("type")
        if mtype == "joined":
            self._ice_servers = msg.get("iceServers", [])
        elif mtype == "offer":
            await self._on_offer(ws, msg)
        elif mtype == "candidate":
            pc = self._pcs.get(msg.get("from"))
            cand_info = msg.get("candidate") or {}
            cand_str = (cand_info.get("candidate") or "").strip() \
                if isinstance(cand_info, dict) else ""
            # An empty candidate string is the browser's end-of-candidates
            # marker — not a real candidate; skip it (aiortc asserts on it).
            if pc and cand_str:
                from aiortc.sdp import candidate_from_sdp  # type: ignore
                if cand_str.startswith("candidate:"):
                    cand_str = cand_str[len("candidate:"):]
                cand = candidate_from_sdp(cand_str)
                cand.sdpMid = cand_info.get("sdpMid")
                cand.sdpMLineIndex = cand_info.get("sdpMLineIndex")
                await pc.addIceCandidate(cand)
        elif mtype == "peer-left":
            pc = self._pcs.pop(msg.get("peer_id"), None)
            if pc:
                await pc.close()

    async def _on_offer(self, ws, msg: dict) -> None:
        viewer = msg["from"]
        config = RTCConfiguration([
            RTCIceServer(**s) for s in (self._ice_servers or
                                        [{"urls": "stun:stun.l.google.com:19302"}])
        ])
        pc = RTCPeerConnection(config)
        self._pcs[viewer] = pc
        self._ensure_sources()
        # Subscribe each viewer to the shared camera sources (one open device,
        # many viewers) instead of opening the camera per connection.
        pc.addTrack(self._relay.subscribe(self._global_src))
        pc.addTrack(self._relay.subscribe(self._wrist_src))

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            log.info("viewer %s connection: %s", viewer, pc.connectionState)
            if pc.connectionState in ("failed", "closed"):
                self._pcs.pop(viewer, None)

        await pc.setRemoteDescription(
            RTCSessionDescription(sdp=msg["sdp"], type="offer"))
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
        await ws.send(json.dumps({
            "type": "answer", "to": viewer,
            "sdp": pc.localDescription.sdp,
        }))

    async def close(self) -> None:
        self._stop = True
        for pc in list(self._pcs.values()):
            await pc.close()
        self._pcs.clear()


def make_video_publisher(signaling_url: str, session_id: str,
                         peer_id: str = "follower-video",
                         transport: str = "webrtc",
                         video_format: str = "binary",
                         global_cfg: Optional["CameraConfig"] = None,
                         wrist_cfg: Optional["CameraConfig"] = None,
                         global_cam=None,
                         wrist_cam=None):
    """Build the video publisher for the chosen transport.

    ``transport``: ``"webrtc"`` (default, real codec over RTP) or ``"websocket"``
    (JPEG frames over the signaling relay). ``video_format`` (``"binary"`` |
    ``"base64"``) applies only to the websocket transport. Both publishers expose
    the same ``run()`` / ``close()`` interface so call sites are identical.
    """
    if transport == "websocket":
        from .ws_publisher import WebSocketVideoPublisher
        return WebSocketVideoPublisher(
            signaling_url, session_id, peer_id,
            global_cfg=global_cfg, wrist_cfg=wrist_cfg,
            global_cam=global_cam, wrist_cam=wrist_cam,
            video_format=video_format,
        )
    if transport != "webrtc":
        raise ValueError(f"transport must be 'webrtc' or 'websocket', got {transport!r}")
    return VideoPublisher(
        signaling_url, session_id, peer_id,
        global_cfg=global_cfg, wrist_cfg=wrist_cfg,
        global_cam=global_cam, wrist_cam=wrist_cam,
    )

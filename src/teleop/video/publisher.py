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
from typing import Dict, List, Optional, Tuple

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
        """Wraps a camera source as a WebRTC video track at the configured FPS.

        One instance per viewer, never shared between peer connections: each
        recv() builds a fresh av.VideoFrame, so no two encoder threads ever
        touch the same native frame (sharing one frame across viewers is a
        data race in PyAV/libvpx that segfaults the process). The *camera*
        may be shared — its read() must be thread-safe and return a copy.
        """

        def __init__(self, cfg: CameraConfig, cam=None) -> None:
            super().__init__()
            self.cfg = cfg
            self._own_cam = cam is None   # only close devices we opened
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
            if self._own_cam:
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
        "RTCIceTransport is closed",                   # pc closed mid-ICE (re-offer)
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
                 wrist_cam=None,
                 cameras: Optional[List[Tuple[CameraConfig, object]]] = None) -> None:
        if not _HAVE_WEBRTC:
            raise RuntimeError(
                "WebRTC deps missing. Install with: pip install -e '.[video]' "
                "(aiortc, av, websockets)."
            )
        self.url = signaling_url.rstrip("/") + f"/ws/{session_id}/{peer_id}"
        self.peer_id = peer_id
        # ``cameras`` is the general form: an ordered list of (config, camera)
        # pairs, one track per entry (camera None -> opened from the config).
        # Track order on the wire == list order. The global/wrist kwargs remain
        # as the two-camera shorthand.
        if cameras is None:
            cameras = [
                (global_cfg or CameraConfig("global", 1280, 720, 30), global_cam),
                (wrist_cfg or CameraConfig("wrist", 640, 480, 30), wrist_cam),
            ]
        self._camera_defs = cameras
        self._pcs: Dict[str, "RTCPeerConnection"] = {}
        self._tracks: Dict[str, list] = {}   # viewer -> its CameraTracks
        self._ice_servers: list = []
        self._stop = False
        self._cams: Optional[list] = None    # [(cfg, camera)] shared by viewers
        self._owned_cams: list = []          # cameras we opened -> we close

    def _ensure_cameras(self) -> None:
        """Open each camera once; viewers get their own tracks over these
        shared sources (one open device, many viewers)."""
        if self._cams is None:
            self._cams = []
            for cfg, cam in self._camera_defs:
                if cam is None:
                    cam = make_camera(cfg)
                    self._owned_cams.append(cam)
                self._cams.append((cfg, cam))
            log.info("camera sources ready (%s)",
                     ", ".join(cfg.name for cfg, _ in self._camera_defs))

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
            await self._teardown_viewer(msg.get("peer_id"))

    async def _teardown_viewer(self, viewer) -> None:
        """Fully release one viewer: stop its tracks (so their sender loops
        exit and no encoder is fed a frame mid-close), then close the pc.
        Idempotent — safe to call from re-offer, peer-left, state change and
        shutdown in any order."""
        pc = self._pcs.pop(viewer, None)
        for track in self._tracks.pop(viewer, []):
            track.stop()
        if pc is not None:
            try:
                await pc.close()
            except Exception:
                log.debug("closing pc for %s failed", viewer)

    async def _on_offer(self, ws, msg: dict) -> None:
        viewer = msg["from"]
        # A re-offer from the same viewer (page refresh, signaling reconnect)
        # must tear down the previous connection first: silently replacing it
        # in _pcs leaves its native encoders running until GC, which can
        # segfault the process (exit code -11).
        await self._teardown_viewer(viewer)
        config = RTCConfiguration([
            RTCIceServer(**s) for s in (self._ice_servers or
                                        [{"urls": "stun:stun.l.google.com:19302"}])
        ])
        pc = RTCPeerConnection(config)
        self._pcs[viewer] = pc
        self._ensure_cameras()
        # Fresh CameraTrack per viewer over the shared cameras: tracks (and
        # the av.VideoFrames they produce) must never be shared between peer
        # connections — concurrent encoders on one frame is a native data
        # race (SIGSEGV in vpx encode). Each track pulls the newest frame
        # from its camera on recv(), so viewers stay live with no buffering.
        # Never add more tracks than the offer has video m-lines: aiortc cannot
        # answer with excess local tracks (ValueError in setLocalDescription).
        # An older viewer that only offers 2 slots just gets the first 2 cameras.
        n_video = msg["sdp"].count("m=video")
        tracks = [CameraTrack(cfg, cam) for cfg, cam in self._cams[:n_video]]
        self._tracks[viewer] = tracks
        for track in tracks:
            pc.addTrack(track)
        if n_video < len(self._cams):
            log.warning("viewer %s offered %d video slot(s) < %d cameras; "
                        "sending the first %d (is the UI up to date?)",
                        viewer, n_video, len(self._cams), n_video)

        @pc.on("connectionstatechange")
        async def _on_state() -> None:
            log.info("viewer %s connection: %s", viewer, pc.connectionState)
            # Guard: a late 'closed' from a pc we already replaced (page
            # refresh) must not tear down the viewer's *new* connection.
            if pc.connectionState in ("failed", "closed") \
                    and self._pcs.get(viewer) is pc:
                await self._teardown_viewer(viewer)

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
        for viewer in list(self._pcs):
            await self._teardown_viewer(viewer)
        for cam in self._owned_cams:
            cam.close()
        self._owned_cams.clear()
        self._cams = None


def make_video_publisher(signaling_url: str, session_id: str,
                         peer_id: str = "follower-video",
                         transport: str = "webrtc",
                         video_format: str = "binary",
                         global_cfg: Optional["CameraConfig"] = None,
                         wrist_cfg: Optional["CameraConfig"] = None,
                         global_cam=None,
                         wrist_cam=None,
                         cameras=None):
    """Build the video publisher for the chosen transport.

    ``transport``: ``"webrtc"`` (default, real codec over RTP) or ``"websocket"``
    (JPEG frames over the signaling relay). ``video_format`` (``"binary"`` |
    ``"base64"``) applies only to the websocket transport. Both publishers expose
    the same ``run()`` / ``close()`` interface so call sites are identical.

    ``cameras``: ordered list of (CameraConfig, camera) pairs for N-camera
    publishing; when given it supersedes the global/wrist two-camera kwargs.
    """
    if transport == "websocket":
        from .ws_publisher import WebSocketVideoPublisher
        return WebSocketVideoPublisher(
            signaling_url, session_id, peer_id,
            global_cfg=global_cfg, wrist_cfg=wrist_cfg,
            global_cam=global_cam, wrist_cam=wrist_cam,
            video_format=video_format, cameras=cameras,
        )
    if transport != "webrtc":
        raise ValueError(f"transport must be 'webrtc' or 'websocket', got {transport!r}")
    return VideoPublisher(
        signaling_url, session_id, peer_id,
        global_cfg=global_cfg, wrist_cfg=wrist_cfg,
        global_cam=global_cam, wrist_cam=wrist_cam,
        cameras=cameras,
    )

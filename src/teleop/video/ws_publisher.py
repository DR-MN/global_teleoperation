"""Follower-side WebSocket video publisher (JPEG over the signaling relay).

An alternative to the WebRTC path in ``publisher.py`` for A/B comparison. Instead
of negotiating a peer connection and streaming a real codec, this captures frames
from the same camera sources, JPEG-encodes them, and pushes them to the signaling
server, which broadcasts them to every viewer in the session.

Two wire formats (choose with ``video_format``):
  * ``"binary"`` — a WebSocket binary frame: 1 byte camera id + raw JPEG bytes.
    Smallest/fastest; no text envelope.
  * ``"base64"`` — a JSON text message ``{"type":"video-frame","cam":..,"data":..}``.
    ~33% larger but self-describing and easy to inspect.

Trade-offs vs WebRTC: simpler (no ICE/SDP), but higher bandwidth (no inter-frame
compression), higher latency under load (TCP head-of-line blocking), and no
adaptive bitrate. Intended as a comparison/debug transport, not the default.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Optional

from .camera import CameraConfig, make_camera

log = logging.getLogger(__name__)

try:
    import cv2  # type: ignore
    import websockets  # type: ignore
    _HAVE_WS_VIDEO = True
except Exception:  # pragma: no cover - optional deps
    _HAVE_WS_VIDEO = False


class WebSocketVideoPublisher:
    """Connects to the signaling server as a 'follower' and pushes JPEG frames to
    all viewers via the relay. One instance serves every viewer in the session."""

    def __init__(self, signaling_url: str, session_id: str,
                 peer_id: str = "follower-video",
                 global_cfg: Optional[CameraConfig] = None,
                 wrist_cfg: Optional[CameraConfig] = None,
                 global_cam=None,
                 wrist_cam=None,
                 video_format: str = "binary",
                 jpeg_quality: int = 80) -> None:
        if not _HAVE_WS_VIDEO:
            raise RuntimeError(
                "WebSocket video deps missing. Install OpenCV + websockets: "
                "pip install opencv-python websockets"
            )
        if video_format not in ("binary", "base64"):
            raise ValueError(f"video_format must be 'binary' or 'base64', got {video_format!r}")
        self.url = signaling_url.rstrip("/") + f"/ws/{session_id}/{peer_id}"
        self.peer_id = peer_id
        self.format = video_format
        self.jpeg_quality = jpeg_quality
        self.global_cfg = global_cfg or CameraConfig("global", 1280, 720, 30)
        self.wrist_cfg = wrist_cfg or CameraConfig("wrist", 640, 480, 30)
        self._global_cam = global_cam
        self._wrist_cam = wrist_cam
        self._stop = False

    async def run(self) -> None:
        backoff = 1.0
        while not self._stop:
            try:
                await self._session()
                backoff = 1.0
            except Exception:
                log.exception("ws video session error; reconnecting in %.1fs", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _session(self) -> None:
        # (camera id, camera source, config). Reuse a pre-built camera (e.g.
        # ROS2Camera) when provided, else open one from the config.
        cams = [
            (0, self._global_cam or make_camera(self.global_cfg), self.global_cfg),
            (1, self._wrist_cam or make_camera(self.wrist_cfg), self.wrist_cfg),
        ]
        fps = max(self.global_cfg.fps, self.wrist_cfg.fps, 1)
        period = 1.0 / fps

        async with websockets.connect(self.url) as ws:
            await ws.send(json.dumps({
                "type": "join", "role": "follower",
                "video": {"transport": "websocket", "format": self.format},
            }))
            log.info("ws video publisher joined %s (format=%s)", self.url, self.format)

            # Drain inbound control messages (joined/peer-joined/peer-left) so the
            # receive buffer doesn't grow while we only ever send frames.
            drain = asyncio.create_task(self._drain(ws))
            try:
                loop = asyncio.get_running_loop()
                while not self._stop:
                    t0 = time.time()
                    for cam_id, cam, _cfg in cams:
                        # cam.read() can block on real devices — run it off the
                        # event loop so frame capture doesn't stall the sender.
                        frame = await loop.run_in_executor(None, cam.read)
                        ok, jpg = cv2.imencode(
                            ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
                        if not ok:
                            continue
                        data = jpg.tobytes()
                        if self.format == "base64":
                            await ws.send(json.dumps({
                                "type": "video-frame", "cam": cam_id,
                                "format": "base64",
                                "data": base64.b64encode(data).decode("ascii"),
                            }))
                        else:
                            await ws.send(bytes([cam_id]) + data)
                    await asyncio.sleep(max(0.0, period - (time.time() - t0)))
            finally:
                drain.cancel()

    async def _drain(self, ws) -> None:
        try:
            async for _ in ws:
                pass  # control messages only; frames are one-way to viewers
        except Exception:
            pass

    async def close(self) -> None:
        self._stop = True

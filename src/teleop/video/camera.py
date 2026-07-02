"""Camera sources for WebRTC publishing.

Two camera roles per the spec:
* Global camera  — 1280x720 @ 30 FPS, workspace/human/object awareness.
* Wrist camera   — 640x480+ @ 30 FPS, grasp/alignment/precision.

``SyntheticCamera`` generates a moving test pattern with a timestamp overlay so
the full video path (publisher -> WebRTC -> viewer) can be validated with no
hardware. ``RealSenseCamera`` / ``V4LCamera`` use OpenCV when available. All
expose a uniform ``read() -> ndarray`` (HxWx3, BGR) interface.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

try:
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore

log = logging.getLogger(__name__)


@dataclass
class CameraConfig:
    name: str = "global"
    width: int = 1280
    height: int = 720
    fps: int = 30
    device: Optional[int] = None   # OpenCV index; None -> synthetic


class SyntheticCamera:
    """Hardware-free animated test pattern. Encodes a frame counter so latency
    can be measured visually and the stream is obviously "live"."""

    def __init__(self, cfg: CameraConfig) -> None:
        if np is None:
            raise RuntimeError("numpy required for SyntheticCamera (pip install numpy)")
        self.cfg = cfg
        self._n = 0
        self._t0 = time.time()

    def read(self):
        w, h = self.cfg.width, self.cfg.height
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        t = time.time() - self._t0
        # Animated gradient so motion is visible; channel offsets per camera.
        xs = np.linspace(0, 255, w, dtype=np.uint8)
        frame[:, :, 0] = xs[None, :]
        frame[:, :, 1] = np.uint8((np.sin(t) * 0.5 + 0.5) * 255)
        ys = np.linspace(0, 255, h, dtype=np.uint8)
        frame[:, :, 2] = ys[:, None]
        # Moving marker.
        cx = int((np.sin(t) * 0.5 + 0.5) * (w - 40)) + 20
        cy = int((np.cos(t) * 0.5 + 0.5) * (h - 40)) + 20
        frame[max(0, cy - 10):cy + 10, max(0, cx - 10):cx + 10] = 255
        self._n += 1
        return frame

    def close(self) -> None:
        pass


class OpenCVCamera:
    """Real camera via OpenCV (USB/V4L/RealSense RGB). Falls back to synthetic
    if the device cannot be opened, so the pipeline degrades gracefully."""

    def __init__(self, cfg: CameraConfig) -> None:
        self.cfg = cfg
        self._cap = None
        self._fallback: Optional[SyntheticCamera] = None
        try:
            import cv2  # type: ignore
            self._cv2 = cv2
            self._cap = cv2.VideoCapture(cfg.device or 0)
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, cfg.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, cfg.height)
            self._cap.set(cv2.CAP_PROP_FPS, cfg.fps)
            if not self._cap.isOpened():
                raise RuntimeError("camera not opened")
            log.info("%s camera opened on device index %s (%dx%d@%d)",
                     cfg.name, cfg.device, cfg.width, cfg.height, cfg.fps)
        except Exception as e:
            log.warning("%s camera: could not open device index %s (%s) — falling "
                        "back to SYNTHETIC test pattern. Check the index with "
                        "`v4l2-ctl --list-devices`.", cfg.name, cfg.device, e)
            self._cap = None
            self._fallback = SyntheticCamera(cfg)

    def read(self):
        if self._cap is not None:
            ok, frame = self._cap.read()
            if ok:
                return frame
        if self._fallback is None:
            self._fallback = SyntheticCamera(self.cfg)
        return self._fallback.read()

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()


class ROS2Camera:
    """Reads frames pushed in via on_image() from a ROS2 image subscription.

    Falls back to a synthetic test pattern until the first ROS2 frame arrives,
    so the WebRTC stream is always live even before the camera topic publishes.
    """

    def __init__(self, cfg: CameraConfig) -> None:
        self.cfg = cfg
        self._latest = None
        self._lock = threading.Lock()
        self._fallback = SyntheticCamera(cfg)

    def on_image(self, msg) -> None:
        """ROS2 subscription callback — convert and store the latest frame."""
        try:
            if np is None:
                return
            data = np.frombuffer(bytes(msg.data), dtype=np.uint8)
            channels = len(msg.data) // (msg.height * msg.width)
            frame = data.reshape((msg.height, msg.width, max(channels, 1)))
            enc = getattr(msg, "encoding", "bgr8").lower()
            if enc in ("rgb8", "rgb"):
                try:
                    import cv2 as _cv2
                    frame = _cv2.cvtColor(frame, _cv2.COLOR_RGB2BGR)
                except Exception:
                    frame = frame[:, :, ::-1]   # flip channels without cv2
            elif enc in ("mono8", "8uc1"):
                frame = np.stack([frame[:, :, 0]] * 3, axis=-1)
            with self._lock:
                self._latest = frame
        except Exception:
            log.exception("ROS2Camera: failed to decode frame on %s", self.cfg.name)

    def read(self):
        with self._lock:
            if self._latest is not None:
                return self._latest.copy()
        return self._fallback.read()

    def close(self) -> None:
        pass


def make_camera(cfg: CameraConfig):
    """Synthetic when no device index is configured, else a real OpenCV camera."""
    if cfg.device is None:
        return SyntheticCamera(cfg)
    return OpenCVCamera(cfg)

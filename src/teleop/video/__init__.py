"""WebRTC video: camera sources + follower-side publisher."""
from .camera import CameraConfig, SyntheticCamera, OpenCVCamera, make_camera

__all__ = ["CameraConfig", "SyntheticCamera", "OpenCVCamera", "make_camera"]

# VideoPublisher is imported lazily to avoid requiring aiortc/av for the core.
def __getattr__(name):  # PEP 562
    if name in ("VideoPublisher", "CameraTrack"):
        from . import publisher
        return getattr(publisher, name)
    raise AttributeError(name)

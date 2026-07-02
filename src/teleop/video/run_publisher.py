"""CLI entrypoint for the follower WebRTC video publisher.

    python -m teleop.video.run_publisher --signaling ws://localhost:8080 \
        --session default
"""
from __future__ import annotations

import argparse
import asyncio
import logging


def main() -> int:
    ap = argparse.ArgumentParser(description="Follower WebRTC video publisher")
    ap.add_argument("--signaling", default="ws://localhost:8080",
                    help="signaling server base URL")
    ap.add_argument("--session", default="default")
    ap.add_argument("--peer-id", default="follower-video")
    ap.add_argument("--global-device", type=int, default=None,
                    help="OpenCV index for the global camera (omit for synthetic)")
    ap.add_argument("--wrist-device", type=int, default=None,
                    help="OpenCV index for the wrist camera (omit for synthetic)")
    ap.add_argument("--video-transport", choices=("webrtc", "websocket"),
                    default="webrtc",
                    help="video transport: webrtc (codec/RTP, default) or "
                         "websocket (JPEG frames over the signaling relay)")
    ap.add_argument("--video-format", choices=("binary", "base64"),
                    default="binary",
                    help="websocket wire format (ignored for webrtc): raw binary "
                         "JPEG or base64 JPEG in JSON")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from .camera import CameraConfig
    from .publisher import make_video_publisher

    pub = make_video_publisher(
        args.signaling, args.session, args.peer_id,
        transport=args.video_transport, video_format=args.video_format,
        global_cfg=CameraConfig("global", 1280, 720, 30, args.global_device),
        wrist_cfg=CameraConfig("wrist", 640, 480, 30, args.wrist_device),
    )
    try:
        asyncio.run(pub.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Follower node — real safety-checked control + (optional) real camera video.

Runs the independent safety controller against the arm and connects to the
leader over the chosen transport. With --video it also publishes the global and
wrist camera feeds over WebRTC (real cameras when device indices are given).

    # Control only:
    python scripts/run_follower.py --transport ws --url wss://HOST --session demo

    # Control + real cameras (OpenCV device indices):
    python scripts/run_follower.py --transport ws --url wss://HOST --session demo \\
        --video --global-cam 0 --wrist-cam 1

To drive a physical follower arm, replace MockArm in FollowerController with your
hardware driver — the safety controller, watchdog, and transport are unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from teleop.core import SystemConfig
from teleop.transport import make_transport
from teleop.follower import FollowerController

log = logging.getLogger("follower")


def main() -> int:
    ap = argparse.ArgumentParser(description="Teleop follower node")
    ap.add_argument("--endpoint", default=os.environ.get("ZENOH_ENDPOINT", ""))
    ap.add_argument("--url", default=os.environ.get("SIGNALING_URL", ""),
                    help="WebSocket server URL for --transport ws")
    ap.add_argument("--session", default=os.environ.get("SESSION_ID", "default"))
    ap.add_argument("--transport", default="ws", choices=["zenoh", "inproc", "ws"])
    ap.add_argument("--video", action="store_true", help="publish camera video streams")
    ap.add_argument("--global-cam", type=int, default=None,
                    help="OpenCV device index for the global camera (real camera)")
    ap.add_argument("--wrist-cam", type=int, default=None,
                    help="OpenCV device index for the wrist camera (real camera)")
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

    cfg = SystemConfig.load()
    cfg.transport = args.transport
    cfg.zenoh_endpoint = args.endpoint or None
    cfg.ws_url = args.url or None
    cfg.session_id = args.session

    tx = make_transport(cfg, role="viewer", peer_id="follower-control")
    follower = FollowerController(cfg, tx)
    follower.start()
    log.info("Follower running (session=%s, transport=%s). Ctrl-C to stop.",
             args.session, args.transport)

    publisher = None
    if args.video:
        from teleop.video.camera import CameraConfig
        from teleop.video.publisher import make_video_publisher
        if args.global_cam is None and args.wrist_cam is None:
            log.warning("--video without --global-cam/--wrist-cam: using synthetic "
                        "test patterns (no real camera attached).")
        publisher = make_video_publisher(
            args.url, args.session, "follower-video",
            transport=args.video_transport, video_format=args.video_format,
            global_cfg=CameraConfig("global", 1280, 720, 30, args.global_cam),
            wrist_cfg=CameraConfig("wrist", 640, 480, 30, args.wrist_cam),
        )

    try:
        if publisher is not None:
            asyncio.run(publisher.run())
        else:
            while True:
                time.sleep(2.0)
                log.info("stats: %s", follower.stats)
    except KeyboardInterrupt:
        pass
    finally:
        follower.stop()
        tx.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

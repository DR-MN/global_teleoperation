"""Camera bridge — streams ROS2 image topics over WebRTC to the operator UI.

Subscribes to ROS2 sensor_msgs/Image topics (one per camera), feeds frames into
the WebRTC VideoPublisher, and streams them to any viewer connected to the
signaling server (browser UI / leader machine). Track order == topic order, so
the UI's tile N shows camera N.

Default (two cameras):
    /global_right_camera/color/image_raw  (sensor_msgs/Image)  — global right view
    /global_left_camera/color/image_raw   (sensor_msgs/Image)  — global left view

Run (inside ROS2 Humble):
    ros2 run teleop_bridge camera_bridge --ros-args \
        -p ws_url:=wss://gt6dof-signaling.onrender.com \
        -p session_id:=demo \
        -p global_right_topic:=/global_right_camera/color/image_raw \
        -p global_left_topic:=/global_left_camera/color/image_raw

    # N cameras (e.g. 4): comma-separated topic list overrides the two above.
    # Order == UI tile order: global right, global left, gripper right, gripper left.
    ros2 run teleop_bridge camera_bridge --ros-args \
        -p ws_url:=wss://gt6dof-signaling.onrender.com \
        -p session_id:=demo \
        -p camera_topics:="/global_right_camera/color/image_raw,/global_left_camera/color/image_raw,/gripper_right_camera/color/image_raw,/gripper_left_camera/color/image_raw"
"""
from __future__ import annotations

import asyncio
import logging
import threading

import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image

# Must match the camera_publisher QoS (best-effort depth-1) or the topic won't
# connect and no frames will flow. Latest-frame-wins for live video.
VIDEO_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
)

from teleop.video.camera import CameraConfig, ROS2Camera
from teleop.video.publisher import make_video_publisher

log = logging.getLogger(__name__)


class CameraBridge(Node):
    def __init__(self) -> None:
        super().__init__("camera_bridge")
        self.declare_parameter("ws_url", "wss://gt6dof-signaling.onrender.com")
        self.declare_parameter("session_id", "demo")
        self.declare_parameter("global_right_topic", "/global_right_camera/color/image_raw")
        self.declare_parameter("global_left_topic", "/global_left_camera/color/image_raw")
        # Comma-separated list of image topics, one camera track per topic (in
        # order). When set, it overrides global_right_topic/global_left_topic.
        self.declare_parameter("camera_topics", "")
        self.declare_parameter("video_transport", "webrtc")   # webrtc | websocket
        self.declare_parameter("video_format", "binary")      # binary | base64

        ws_url     = self.get_parameter("ws_url").value
        session_id = self.get_parameter("session_id").value
        global_right_topic = self.get_parameter("global_right_topic").value
        global_left_topic  = self.get_parameter("global_left_topic").value
        topics_csv   = self.get_parameter("camera_topics").value or ""
        video_transport = self.get_parameter("video_transport").value
        video_format    = self.get_parameter("video_format").value

        topics = [t.strip() for t in topics_csv.split(",") if t.strip()] \
            or [global_right_topic, global_left_topic]

        # Camera instances — frames are pushed in via ROS2 subscription callbacks.
        # Global views are high-res, gripper views 640x480 (the config only sizes
        # the synthetic fallback; real frames pass through at whatever resolution
        # the topic publishes). Order == UI tile order.
        names = ["global_right", "global_left", "gripper_right", "gripper_left"]
        self.cams = []
        for i, topic in enumerate(topics):
            w, h = (1280, 720) if i < 2 else (640, 480)
            name = names[i] if i < len(names) else f"cam{i}"
            cam = ROS2Camera(CameraConfig(name, w, h, 30))
            self.create_subscription(Image, topic, cam.on_image, VIDEO_QOS)
            self.cams.append(cam)

        self._publisher = make_video_publisher(
            ws_url, session_id,
            peer_id="follower-video",
            transport=video_transport, video_format=video_format,
            cameras=[(cam.cfg, cam) for cam in self.cams],
        )

        # VideoPublisher runs an asyncio loop — spin it in a background thread
        # so ROS2 spin() can run on the main thread unblocked.
        self._thread = threading.Thread(target=self._run_video, daemon=True)
        self._thread.start()

        self.get_logger().info(
            f"camera_bridge up (ws_url={ws_url!r}, session={session_id!r}, "
            f"topics={topics!r}, "
            f"transport={video_transport!r}, format={video_format!r})"
        )

    def _run_video(self) -> None:
        asyncio.run(self._publisher.run())

    def destroy_node(self) -> None:
        asyncio.run(self._publisher.close())
        super().destroy_node()


def main() -> None:
    rclpy.init()
    node = CameraBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

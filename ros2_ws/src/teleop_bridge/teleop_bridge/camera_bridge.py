"""Camera bridge — streams ROS2 image topics over WebRTC to the operator UI.

Subscribes to ROS2 sensor_msgs/Image topics for the global and wrist cameras,
feeds frames into the WebRTC VideoPublisher, and streams them to any viewer
connected to the signaling server (browser UI / leader machine).

ROS2 topics subscribed:
    /camera/global/image_raw  (sensor_msgs/Image)  — overhead/workspace view
    /camera/wrist/image_raw   (sensor_msgs/Image)  — wrist/grasp view

Run (inside ROS2 Humble):
    ros2 run teleop_bridge camera_bridge --ros-args \
        -p ws_url:=wss://gt6dof-signaling.onrender.com \
        -p session_id:=demo \
        -p global_topic:=/camera/global/image_raw \
        -p wrist_topic:=/camera/wrist/image_raw
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
        self.declare_parameter("global_topic", "/camera/global/image_raw")
        self.declare_parameter("wrist_topic", "/camera/wrist/image_raw")
        self.declare_parameter("video_transport", "webrtc")   # webrtc | websocket
        self.declare_parameter("video_format", "binary")      # binary | base64

        ws_url     = self.get_parameter("ws_url").value
        session_id = self.get_parameter("session_id").value
        global_topic = self.get_parameter("global_topic").value
        wrist_topic  = self.get_parameter("wrist_topic").value
        video_transport = self.get_parameter("video_transport").value
        video_format    = self.get_parameter("video_format").value

        # Camera instances — frames are pushed in via ROS2 subscription callbacks.
        self.global_cam = ROS2Camera(CameraConfig("global", 1280, 720, 30))
        self.wrist_cam  = ROS2Camera(CameraConfig("wrist",  640,  480, 30))

        self.create_subscription(Image, global_topic, self.global_cam.on_image, VIDEO_QOS)
        self.create_subscription(Image, wrist_topic,  self.wrist_cam.on_image,  VIDEO_QOS)

        self._publisher = make_video_publisher(
            ws_url, session_id,
            peer_id="follower-video",
            transport=video_transport, video_format=video_format,
            global_cam=self.global_cam,
            wrist_cam=self.wrist_cam,
        )

        # VideoPublisher runs an asyncio loop — spin it in a background thread
        # so ROS2 spin() can run on the main thread unblocked.
        self._thread = threading.Thread(target=self._run_video, daemon=True)
        self._thread.start()

        self.get_logger().info(
            f"camera_bridge up (ws_url={ws_url!r}, session={session_id!r}, "
            f"global={global_topic!r}, wrist={wrist_topic!r}, "
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

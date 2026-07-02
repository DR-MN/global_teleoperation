"""Generic OpenCV camera publisher (one node, launched per camera).

Publishes a V4L2/USB camera as sensor_msgs/Image (+ optional CompressedImage)
so camera_bridge can stream it over WebRTC. A single parameterized node replaces
per-camera copies: launch it once per camera with different ``device``/``topic``.

Stable device selection (avoids /dev/videoN shuffling on replug):
    ``device`` accepts EITHER a numeric index ("2") OR a path. Prefer the kernel's
    persistent symlinks so the index never matters:
        /dev/v4l/by-id/usb-<vendor>_<model>_<serial>-video-index0   (per camera)
        /dev/v4l/by-path/pci-...-usb-...-video-index0               (per USB port)

Run:
    ros2 run teleop_bridge camera_publisher --ros-args \
        -p device:=/dev/v4l/by-id/usb-046d_C922_...-video-index0 \
        -p topic:=/global_camera/color/image_raw \
        -p width:=1280 -p height:=720 -p fps:=30 -p frame_id:=global_camera
"""
from __future__ import annotations

import cv2
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import Image, CompressedImage
from cv_bridge import CvBridge

# Live video wants the newest frame, not every frame: best-effort (no retransmit
# stalls) + depth-1 (no stale-frame queue). Publisher and subscriber must match.
VIDEO_QOS = QoSProfile(
    reliability=ReliabilityPolicy.BEST_EFFORT,
    history=HistoryPolicy.KEEP_LAST,
    depth=1,
)


def _open_capture(device: str) -> cv2.VideoCapture:
    """Open a capture from an index ("2") or a device path (/dev/v4l/by-id/...)."""
    src = int(device) if str(device).isdigit() else str(device)
    return cv2.VideoCapture(src, cv2.CAP_V4L2)


class CameraPublisher(Node):
    def __init__(self) -> None:
        super().__init__("camera_publisher")

        self.declare_parameter("device", "0")          # index "0" or a /dev path
        self.declare_parameter("topic", "/camera/color/image_raw")
        self.declare_parameter("frame_id", "camera_link")
        self.declare_parameter("width", 1280)
        self.declare_parameter("height", 720)
        self.declare_parameter("fps", 30.0)
        self.declare_parameter("jpeg_quality", 80)
        self.declare_parameter("publish_compressed", True)

        self.device   = self.get_parameter("device").value
        topic         = self.get_parameter("topic").value
        self.frame_id = self.get_parameter("frame_id").value
        self.width    = int(self.get_parameter("width").value)
        self.height   = int(self.get_parameter("height").value)
        fps           = float(self.get_parameter("fps").value)
        self.jpeg_quality = int(self.get_parameter("jpeg_quality").value)
        self.publish_compressed = bool(self.get_parameter("publish_compressed").value)

        self.publisher = self.create_publisher(Image, topic, VIDEO_QOS)
        self.compressed_publisher = (
            self.create_publisher(CompressedImage, topic + "/compressed", VIDEO_QOS)
            if self.publish_compressed else None
        )
        self.bridge = CvBridge()

        self.cap = self._connect()
        self.timer = self.create_timer(1.0 / fps, self.publish_frame)
        self.get_logger().info(
            f"camera_publisher up (device={self.device!r}, topic={topic!r}, "
            f"{self.width}x{self.height}@{fps:g})"
        )

    def _connect(self) -> cv2.VideoCapture:
        cap = _open_capture(self.device)
        if not cap.isOpened():
            self.get_logger().error(
                f"Failed to open camera {self.device!r}; will retry. "
                f"Tip: use a /dev/v4l/by-id/ path so the index can't shuffle."
            )
            return cap
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # Keep only the newest frame so read() never returns a stale queued one.
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        return cap

    def publish_frame(self) -> None:
        # Re-open transparently if the camera dropped (e.g. was replugged).
        if not self.cap.isOpened():
            self.cap = self._connect()
            return

        ret, frame = self.cap.read()
        if not ret:
            self.get_logger().warning("Failed to grab frame; reconnecting")
            self.cap.release()
            self.cap = self._connect()
            return

        now = self.get_clock().now().to_msg()

        raw_msg = self.bridge.cv2_to_imgmsg(frame, encoding="bgr8")
        raw_msg.header.stamp = now
        raw_msg.header.frame_id = self.frame_id
        self.publisher.publish(raw_msg)

        if self.compressed_publisher is not None:
            compressed_msg = CompressedImage()
            compressed_msg.header.stamp = now
            compressed_msg.header.frame_id = self.frame_id
            compressed_msg.format = "jpeg"
            ok, encoded = cv2.imencode(
                ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality])
            if ok:
                compressed_msg.data = encoded.tobytes()
                self.compressed_publisher.publish(compressed_msg)

    def destroy_node(self) -> None:
        if self.cap is not None:
            self.cap.release()
        super().destroy_node()


def main(args=None) -> None:
    rclpy.init(args=args)
    node = CameraPublisher()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

"""Follower-side ROS2 <-> WebSocket bridge.

Receives leader commands over WebSocket, runs them through the independent
``FollowerController`` (safety + watchdog + arm driver), and republishes the
resulting commands/state onto ROS2 follower topics so a real ros2_control /
MoveIt stack (or a sim) can execute them. Follower state is also fed back over
WebSocket to the remote leader by the controller itself.

ROS2 topics (per ctx.txt):
    sub (via WebSocket): leader commands
    pub: /joint_commands        (sensor_msgs/JointState)
         /follower/status       (std_msgs/String)
         /follower/diagnostics  (std_msgs/String)

Run (inside ROS2 Humble):
    ros2 run teleop_bridge follower_bridge --ros-args \
        -p ws_url:=wss://gt6dof-signaling.onrender.com \
        -p session_id:=demo
"""
from __future__ import annotations

import json

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

from teleop.core import SystemConfig
from teleop.transport import (
    make_transport,
    KEY_LEADER_COMMAND,
    KEY_FOLLOWER_STATUS,
    KEY_FOLLOWER_DIAG,
)
from teleop.follower import FollowerController


class FollowerBridge(Node):
    def __init__(self) -> None:
        super().__init__("follower_bridge")
        self.declare_parameter("transport", "ws")       # "ws" | "zenoh" | "inproc"
        self.declare_parameter("ws_url", "")
        self.declare_parameter("zenoh_endpoint", "")     # e.g. "tcp/router.example.com:7447"
        self.declare_parameter("session_id", "default")
        # Arm selection (override config/system.yaml from the launch file).
        self.declare_parameter("follower_arm", "")     # "" -> use config value
        self.declare_parameter("so101_port", "")       # "" -> use config value

        cfg = SystemConfig.load()
        cfg.transport = self.get_parameter("transport").value or cfg.transport
        cfg.ws_url = self.get_parameter("ws_url").value or None
        cfg.zenoh_endpoint = self.get_parameter("zenoh_endpoint").value or None
        cfg.session_id = self.get_parameter("session_id").value
        cfg.follower_arm = self.get_parameter("follower_arm").value or cfg.follower_arm
        cfg.so101_port = self.get_parameter("so101_port").value or cfg.so101_port
        self.cfg = cfg
        self.tx = make_transport(cfg, peer_id="follower")

        # Drive the configured arm through the safety-checked controller. "mock"
        # uses the built-in sim; "so101" drives the real Feetech SO-101.
        self._joint_names: list = []

        arm = self._make_arm(cfg)
        self.controller = FollowerController(cfg, self.tx, arm=arm)
        self.controller.start()

        self.pub_js = self.create_publisher(JointState, "/follower_joint_commands", 10)
        self.pub_status = self.create_publisher(String, "/follower/status", 10)
        self.pub_diag = self.create_publisher(String, "/follower/diagnostics", 10)
        self.tx.subscribe(KEY_LEADER_COMMAND, self._on_command)
        self.tx.subscribe(KEY_FOLLOWER_STATUS, self._on_status)
        self.tx.subscribe(KEY_FOLLOWER_DIAG, self._on_diag)
        self.get_logger().info(f"follower_bridge up (ws_url={cfg.ws_url!r}, session={cfg.session_id!r})")

    def _make_arm(self, cfg: SystemConfig):
        """Construct the follower arm driver from config. None -> MockArm."""
        arm_kind = (cfg.follower_arm or "mock").lower()
        if arm_kind in ("mock", ""):
            return None  # FollowerController falls back to MockArm
        if arm_kind == "so101":
            from teleop.hardware.so101_arm import SO101Arm
            self.get_logger().info(
                f"Connecting SO-101 follower on {cfg.so101_port} ...")
            arm = SO101Arm(
                dof=cfg.dof,
                max_velocity=cfg.joint_limits.max_velocity,
                port=cfg.so101_port,
                calibration_dir=cfg.so101_calibration_dir,
            )
            self.get_logger().info("SO-101 follower connected.")
            return arm
        raise ValueError(f"unknown follower_arm: {cfg.follower_arm!r}")

    def _on_command(self, payload: dict) -> None:
        if "names" in payload:
            self._joint_names = payload["names"]
        positions = payload.get("positions", [])
        if not positions:
            return
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name = self._joint_names or [f"joint{i}" for i in range(len(positions))]
        js.position = list(positions)
        js.velocity = list(payload.get("velocities", []))
        self.pub_js.publish(js)

    def _on_status(self, payload: dict) -> None:
        self.pub_status.publish(String(data=json.dumps(payload)))

    def _on_diag(self, payload: dict) -> None:
        self.pub_diag.publish(String(data=json.dumps(payload)))

    def destroy_node(self) -> None:
        self.controller.stop()
        disconnect = getattr(self.controller.arm, "disconnect", None)
        if callable(disconnect):
            disconnect()
        self.tx.close()
        super().destroy_node()


def main() -> None:
    rclpy.init()
    node = FollowerBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

"""Leader-side ROS2 <-> WebSocket bridge.

Subscribes /joint_cmd (all joints including gripper; the launch file remaps
this onto /leader_joint_states when a real leader arm is selected) and
forwards it as-is over the transport to the remote follower.

Two modes, selected by the ``leader_arm`` parameter:

* ``none`` (default) — pure topic relay: forward only, no feedback path.

* anything else (``so101`` | ``piper``) — arm mode: additionally mirrors the
  remote follower's state/status feedback onto local ROS2 topics for
  visualization.

ROS2 topics:
    sub:  /joint_cmd              (sensor_msgs/JointState) — all joints incl. gripper
    pub:  /follower/joint_states  (sensor_msgs/JointState) — arm mode only
          /follower/status        (std_msgs/String)        — arm mode only

Run (inside ROS2 Humble):
    ros2 run teleop_bridge leader_bridge --ros-args \
        -p ws_url:=wss://gt6dof-signaling.onrender.com \
        -p session_id:=demo
"""
from __future__ import annotations

import json

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import JointState
from std_msgs.msg import String

from teleop.core import SystemConfig, JointCommand, RobotState, now
from teleop.transport import (
    make_transport, KEY_LEADER_COMMAND, KEY_FOLLOWER_STATE, KEY_FOLLOWER_STATUS,
)


class LeaderBridge(Node):
    def __init__(self) -> None:
        super().__init__("leader_bridge")
        self.declare_parameter("transport", "ws")       # "ws" | "zenoh" | "inproc"
        self.declare_parameter("ws_url", "")
        self.declare_parameter("zenoh_endpoint", "")     # e.g. "tcp/router.example.com:7447"
        self.declare_parameter("session_id", "default")
        # "none" -> pure topic relay; "so101" | "piper" -> also mirror the
        # follower's state/status feedback onto local ROS2 topics.
        self.declare_parameter("leader_arm", "none")

        cfg = SystemConfig.load()
        cfg.transport = self.get_parameter("transport").value or cfg.transport
        cfg.ws_url = self.get_parameter("ws_url").value or None
        cfg.zenoh_endpoint = self.get_parameter("zenoh_endpoint").value or None
        cfg.session_id = self.get_parameter("session_id").value
        self.cfg = cfg
        self.tx = make_transport(cfg, peer_id="leader")
        self._seq = 0

        self.create_subscription(JointState, "/joint_cmd", self._on_joints, 10)

        arm_kind = (self.get_parameter("leader_arm").value or "none").lower()
        if arm_kind != "none":
            self.pub_follower = self.create_publisher(
                JointState, "/follower/joint_states", 10)
            self.pub_status = self.create_publisher(String, "/follower/status", 10)
            self.tx.subscribe(KEY_FOLLOWER_STATE, self._on_follower_state)
            self.tx.subscribe(KEY_FOLLOWER_STATUS, self._on_follower_status)

        self.get_logger().info(
            f"leader_bridge up (arm={arm_kind!r}, ws_url={cfg.ws_url!r}, "
            f"session={cfg.session_id!r})")

    def _on_joints(self, msg: JointState) -> None:
        self._seq += 1
        cmd = JointCommand(
            seq=self._seq, stamp=now(),
            positions=list(msg.position),
            velocities=list(msg.velocity),
        )
        payload = cmd.to_dict()
        payload["names"] = list(msg.name)
        self.tx.publish(KEY_LEADER_COMMAND, payload)

    def _on_follower_state(self, payload: dict) -> None:
        st = RobotState.from_dict(payload)
        js = JointState()
        js.header.stamp = self.get_clock().now().to_msg()
        js.name = [f"joint{i}" for i in range(len(st.positions))]
        js.position = list(st.positions)
        js.velocity = list(st.velocities)
        self.pub_follower.publish(js)

    def _on_follower_status(self, payload: dict) -> None:
        self.pub_status.publish(String(data=json.dumps(payload)))

    def destroy_node(self) -> None:
        self.tx.close()
        super().destroy_node()


def main() -> None:
    rclpy.init()
    node = LeaderBridge()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()

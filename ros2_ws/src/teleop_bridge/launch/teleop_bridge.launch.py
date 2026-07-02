"""Launch leader bridge, follower bridge, camera bridge, or all.

    # leader only:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=leader \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo

    # follower only:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo

    # follower + real SO-101 + cameras (robot side); stable by-id device paths:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        follower_arm:=so101 follower_port:=/dev/ttyACM0 \
        with_camera:=true \
        global_cam_device:=/dev/v4l/by-id/usb-046d_Webcam_A-video-index0 \
        gripper_cam_device:=/dev/v4l/by-id/usb-046d_Webcam_B-video-index0

    # everything on one machine (testing, cameras by index):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=both \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        with_camera:=true global_cam_device:=0 gripper_cam_device:=2

    # Piper arm (run can_activate.sh first; e.g. bash ~/piper_sdk/piper_sdk/can_activate.sh can0 1000000).
    # Uses piper_single_ctrl on each side (no leader/slave readers).
    #   leader machine (arm on can0, back-drivable):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=leader \
        ws_url:=wss://HOST session_id:=demo leader_arm:=piper leader_can_port:=can0
    #   follower machine (arm on can0, driven):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://HOST session_id:=demo follower_arm:=piper follower_can_port:=can0
    #   BOTH arms on one machine: leader on can0, follower on can1 (activate both first):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=both \
        ws_url:=wss://HOST session_id:=demo \
        leader_arm:=piper leader_can_port:=can0 \
        follower_arm:=piper follower_can_port:=can1
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node


def generate_launch_description() -> LaunchDescription:
    side         = LaunchConfiguration("side")
    transport    = LaunchConfiguration("transport")
    ws_url       = LaunchConfiguration("ws_url")
    zenoh_ep     = LaunchConfiguration("zenoh_endpoint")
    session      = LaunchConfiguration("session_id")
    with_camera  = LaunchConfiguration("with_camera")
    global_topic = LaunchConfiguration("global_topic")
    wrist_topic  = LaunchConfiguration("wrist_topic")
    leader_arm      = LaunchConfiguration("leader_arm")
    follower_arm    = LaunchConfiguration("follower_arm")
    leader_port     = LaunchConfiguration("leader_port")
    follower_port   = LaunchConfiguration("follower_port")
    calibration_dir = LaunchConfiguration("calibration_dir")
    global_cam_device  = LaunchConfiguration("global_cam_device")
    gripper_cam_device = LaunchConfiguration("gripper_cam_device")
    video_transport = LaunchConfiguration("video_transport")
    video_format    = LaunchConfiguration("video_format")
    leader_can_port    = LaunchConfiguration("leader_can_port")
    follower_can_port  = LaunchConfiguration("follower_can_port")
    gripper_exist      = LaunchConfiguration("gripper_exist")

    shared_params = [{"transport": transport, "ws_url": ws_url,
                      "zenoh_endpoint": zenoh_ep, "session_id": session}]

    on_leader   = "' in ('leader', 'both')"
    on_follower = "' in ('follower', 'both')"

    # Launch the SO-101 leader publisher only when explicitly asked for (it
    # needs the physical arm), and only on the leader/both side.
    so101_leader_if = IfCondition(PythonExpression(
        ["'", leader_arm, "' == 'so101' and '", side, on_leader]
    ))
    # Piper uses the SAME node (piper_single_ctrl) on both sides: it publishes the
    # arm's joint states (read) and subscribes joint_ctrl_single (drive). One node
    # per CAN bus, namespaced so two arms can share one machine (can0 + can1).
    piper_leader_if = IfCondition(PythonExpression(
        ["'", leader_arm, "' == 'piper' and '", side, on_leader]
    ))
    piper_follower_if = IfCondition(PythonExpression(
        ["'", follower_arm, "' == 'piper' and '", side, on_follower]
    ))
    # The bridge itself drives SO-101 directly; for Piper (external CAN driver) it
    # runs the mock arm and just relays the command topic.
    bridge_follower_arm = PythonExpression(
        ["'mock' if '", follower_arm, "' == 'piper' else '", follower_arm, "'"]
    )

    return LaunchDescription([
        DeclareLaunchArgument("side", default_value="both",
                              description="leader | follower | both"),
        DeclareLaunchArgument("ws_url",
                              default_value="wss://gt6dof-signaling.onrender.com"),
        DeclareLaunchArgument("session_id", default_value="demo"),
        DeclareLaunchArgument("transport", default_value="ws",
                              description="control-plane transport: ws | zenoh | inproc"),
        DeclareLaunchArgument("zenoh_endpoint", default_value="",
                              description="Zenoh router endpoint, e.g. tcp/router.example.com:7447"),
        DeclareLaunchArgument("with_camera", default_value="false",
                              description="true to also launch the cameras + camera_bridge"),
        DeclareLaunchArgument("global_topic",
                              default_value="/global_camera/color/image_raw"),
        DeclareLaunchArgument("wrist_topic",
                              default_value="/gripper_camera/color/image_raw"),
        DeclareLaunchArgument("global_cam_device", default_value="2",
                              description="global camera: index ('0') or /dev/v4l/by-id/... path"),
        DeclareLaunchArgument("gripper_cam_device", default_value="6",
                              description="gripper camera: index ('1') or /dev/v4l/by-id/... path"),
        DeclareLaunchArgument("video_transport", default_value="webrtc",
                              description="video transport: webrtc | websocket"),
        DeclareLaunchArgument("video_format", default_value="binary",
                              description="websocket wire format: binary | base64 (ignored for webrtc)"),
        DeclareLaunchArgument("leader_arm", default_value="none",
                              description="leader arm reader: 'none' | 'so101' | 'piper'"),
        DeclareLaunchArgument("follower_arm", default_value="",
                              description="follower arm: 'mock' | 'so101' | 'piper' ('' uses config)"),
        DeclareLaunchArgument("leader_port", default_value="/dev/ttyACM1",
                              description="SO-101 leader serial port"),
        DeclareLaunchArgument("follower_port", default_value="",
                              description="SO-101 follower serial port ('' uses config)"),
        DeclareLaunchArgument("calibration_dir", default_value="",
                              description="SO-101 calibration dir ('' uses package .cache)"),
        DeclareLaunchArgument("leader_can_port", default_value="can0",
                              description="Piper leader CAN port (run can_activate.sh first)"),
        DeclareLaunchArgument("follower_can_port", default_value="can0",
                              description="Piper follower CAN port (use can1 for a 2nd arm on one PC)"),
        DeclareLaunchArgument("gripper_exist", default_value="true",
                              description="Piper: arm has a gripper"),

        # SO-101 leader arm reader -> publishes /leader_joint_states (radians),
        # which leader_bridge already subscribes to.
        Node(
            package="so101_ros2",
            executable="so101_ros2_pub_with_conversion",
            name="so101_leader_publisher",
            output="screen",
            emulate_tty=True,
            parameters=[{
                "robot_name": "so101_leader",
                "port": leader_port,
                "calibration_dir": calibration_dir,
            }],
            condition=so101_leader_if,
        ),

        # Piper LEADER: piper_single_ctrl reads the leader arm and publishes its
        # state on 'follower_joint_states' -> remapped to /leader_joint_states for
        # leader_bridge. Proper bring-up: enable the arm, then disable it once
        # enable is confirmed so it's back-drivable (disable_after_enable=true).
        # Namespaced so it can coexist with a follower arm on one machine.
        Node(
            package="piper",
            executable="piper_single_ctrl",
            name="piper_ctrl_single_node",
            namespace="piper_leader",
            output="screen",
            parameters=[{
                "can_port": leader_can_port,
                "auto_enable": True,
                "disable_after_enable": True,
                "gripper_exist": gripper_exist,
            }],
            remappings=[("follower_joint_states", "/leader_joint_states")],
            condition=piper_leader_if,
        ),

        Node(
            package="teleop_bridge",
            executable="leader_bridge",
            name="leader_bridge",
            output="screen",
            parameters=shared_params,
            condition=IfCondition(
                PythonExpression(["'", side, "' in ('leader', 'both')"])
            ),
        ),
        Node(
            package="teleop_bridge",
            executable="follower_bridge",
            name="follower_bridge",
            output="screen",
            parameters=[{
                "transport": transport,
                "ws_url": ws_url,
                "zenoh_endpoint": zenoh_ep,
                "session_id": session,
                "follower_arm": bridge_follower_arm,
                "so101_port": follower_port,
            }],
            condition=IfCondition(
                PythonExpression(["'", side, "' in ('follower', 'both')"])
            ),
        ),

        # Piper FOLLOWER: piper_single_ctrl drives the arm over CAN from
        # joint_ctrl_single -> remapped to /follower_joint_commands (what the
        # bridge publishes). auto_enable on. Namespaced + its own CAN port so a
        # second arm (can1) can run on the same machine as the leader (can0).
        Node(
            package="piper",
            executable="piper_single_ctrl",
            name="piper_ctrl_single_node",
            namespace="piper_follower",
            output="screen",
            parameters=[{
                "can_port": follower_can_port,
                "auto_enable": True,
                "gripper_exist": gripper_exist,
            }],
            remappings=[("joint_ctrl_single", "/follower_joint_commands")],
            condition=piper_follower_if,
        ),
        # Camera source nodes (follower/robot side). device accepts an index or a
        # stable /dev/v4l/by-id|by-path/... symlink so the index can't shuffle.
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="global_camera",
            output="screen",
            parameters=[{
                "device": global_cam_device,
                "topic": global_topic,
                "frame_id": "global_camera",
                "width": 1280, "height": 720, "fps": 30.0,
            }],
            condition=IfCondition(with_camera),
        ),
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="gripper_camera",
            output="screen",
            parameters=[{
                "device": gripper_cam_device,
                "topic": wrist_topic,
                "frame_id": "gripper_camera",
                "width": 640, "height": 480, "fps": 30.0,
            }],
            condition=IfCondition(with_camera),
        ),
        Node(
            package="teleop_bridge",
            executable="camera_bridge",
            name="camera_bridge",
            output="screen",
            parameters=[{
                "ws_url": ws_url,
                "session_id": session,
                "global_topic": global_topic,
                "wrist_topic": wrist_topic,
                "video_transport": video_transport,
                "video_format": video_format,
            }],
            condition=IfCondition(with_camera),
        ),
    ])

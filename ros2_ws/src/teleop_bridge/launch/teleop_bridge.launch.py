"""Launch leader bridge, follower bridge, camera bridge, or all.

Two ways to use it, selected by leader_arm / follower_arm:

1. Pure topic relay (default, leader_arm:=none follower_arm:=none):
   leader forwards /joint_cmd over the transport, follower republishes it
   locally as /joint_cmd. Whatever publishes/consumes /joint_cmd on each
   machine is out of scope here.

2. Robot mode (leader_arm / follower_arm := so101 | piper):
   the bridges switch to their arm logic and /joint_cmd is NOT used; the
   robot topics from the older setup are:
       leader arm driver -> /leader_joint_states -> leader_bridge
           -> transport -> follower_bridge (FollowerController:
              safety + watchdog + arm driver) -> /follower_joint_commands
   * follower so101: the bridge drives the arm itself (serial, so101_port).
   * follower piper: the bridge runs the mock arm and relays the command
     topic; the external piper CAN node consumes /follower_joint_commands.
   * leader: the bridge also mirrors follower state/status feedback onto
     /follower/joint_states and /follower/status.

Cameras always live on the follower/robot side and launch by default
(with_camera:=false to disable).

    # topic relay, leader only:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=leader \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo

    # topic relay, follower only:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo

    # SO-101 leader (real arm on serial):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=leader \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        leader_arm:=so101 leader_port:=/dev/ttyACM1

    # SO-101 follower + local USB cameras (prefer stable /dev/v4l/by-id paths):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        follower_arm:=so101 follower_port:=/dev/ttyACM0 \
        global_right_device:=/dev/v4l/by-id/usb-046d_Webcam_A-video-index0 \
        global_left_device:=/dev/v4l/by-id/usb-046d_Webcam_B-video-index0

    # Piper arm (run can_activate.sh first; e.g.
    # bash ~/piper_sdk/piper_sdk/can_activate.sh can0 1000000).
    #   leader machine (arm on can0, back-drivable):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=leader \
        ws_url:=wss://HOST session_id:=demo leader_arm:=piper leader_can_port:=can0
    #   follower machine (arm on can0, driven):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://HOST session_id:=demo follower_arm:=piper follower_can_port:=can0
    #   BOTH arms on one machine: leader on can0, follower on can1 (activate both):
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=both \
        ws_url:=wss://HOST session_id:=demo \
        leader_arm:=piper leader_can_port:=can0 \
        follower_arm:=piper follower_can_port:=can1

    # four local USB cameras.
    # UI tile order: global right, global left, gripper right, gripper left.
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        global_right_device:=0 global_left_device:=2 \
        gripper_right_device:=4 gripper_left_device:=6

    # externally published topics (e.g. a ZED node): NO devices — just point the
    # topic args at the existing topics; only camera_bridge is launched:
    ros2 launch teleop_bridge teleop_bridge.launch.py side:=follower \
        ws_url:=wss://gt6dof-signaling.onrender.com session_id:=demo \
        global_right_topic:=/zed/zed_node/right/image_rect_color \
        global_left_topic:=/zed/zed_node/left/image_rect_color \
        gripper_right_topic:=/zed2/zed_node/right/image_rect_color \
        gripper_left_topic:=/zed2/zed_node/left/image_rect_color
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration, PythonExpression
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description() -> LaunchDescription:
    side         = LaunchConfiguration("side")
    transport    = LaunchConfiguration("transport")
    ws_url       = LaunchConfiguration("ws_url")
    zenoh_ep     = LaunchConfiguration("zenoh_endpoint")
    session      = LaunchConfiguration("session_id")
    with_camera  = LaunchConfiguration("with_camera")
    leader_arm      = LaunchConfiguration("leader_arm")
    follower_arm    = LaunchConfiguration("follower_arm")
    leader_port     = LaunchConfiguration("leader_port")
    follower_port   = LaunchConfiguration("follower_port")
    calibration_dir = LaunchConfiguration("calibration_dir")
    leader_can_port    = LaunchConfiguration("leader_can_port")
    follower_can_port  = LaunchConfiguration("follower_can_port")
    gripper_exist      = LaunchConfiguration("gripper_exist")
    global_right_topic  = LaunchConfiguration("global_right_topic")
    global_left_topic   = LaunchConfiguration("global_left_topic")
    gripper_right_topic = LaunchConfiguration("gripper_right_topic")
    gripper_left_topic  = LaunchConfiguration("gripper_left_topic")
    global_right_device  = LaunchConfiguration("global_right_device")
    global_left_device   = LaunchConfiguration("global_left_device")
    gripper_right_device = LaunchConfiguration("gripper_right_device")
    gripper_left_device  = LaunchConfiguration("gripper_left_device")
    video_transport = LaunchConfiguration("video_transport")
    video_format    = LaunchConfiguration("video_format")

    on_leader   = "' in ('leader', 'both')"
    on_follower = "' in ('follower', 'both')"

    # Arm driver nodes launch only when their arm type is selected AND we are on
    # the matching side. leader_arm/follower_arm default to 'none' = pure topic
    # relay, no drivers, /joint_cmd in -> /joint_cmd out.
    so101_leader_if = IfCondition(PythonExpression(
        ["'", leader_arm, "' == 'so101' and '", side, on_leader]
    ))
    piper_leader_if = IfCondition(PythonExpression(
        ["'", leader_arm, "' == 'piper' and '", side, on_leader]
    ))
    piper_follower_if = IfCondition(PythonExpression(
        ["'", follower_arm, "' == 'piper' and '", side, on_follower]
    ))

    # The bridges use /joint_cmd internally (leader subscribes, follower
    # publishes). That stays as-is only in the default topic-relay mode. In
    # robot mode the bridges are remapped onto the older robot topics instead:
    # the leader arm driver publishes /leader_joint_states, and the follower
    # arm driver consumes /follower_joint_commands.
    leader_bridge_in = PythonExpression(
        ["'/joint_cmd' if '", leader_arm, "' == 'none' else '/leader_joint_states'"]
    )
    follower_bridge_out = PythonExpression(
        ["'/joint_cmd' if '", follower_arm, "' == 'none' else '/follower_joint_commands'"]
    )
    # The bridge itself drives SO-101 directly (FollowerController + serial);
    # for Piper (external CAN driver) it runs the mock arm and just relays the
    # command topic. 'none' keeps the bridge in pure relay mode.
    bridge_follower_arm = PythonExpression(
        ["'mock' if '", follower_arm, "' == 'piper' else '", follower_arm, "'"]
    )

    # A local camera_publisher runs only when its device is set. When the topic
    # is published by something else (e.g. a ZED node), leave the device empty
    # and just point the topic arg at it — camera_bridge subscribes either way.
    # Cameras belong to the follower/robot side.
    camera_side = ["'", with_camera, "'.lower() in ('true', '1') and '", side, on_follower]
    camera_if = IfCondition(PythonExpression(camera_side))

    def _pub_if(device):
        return IfCondition(PythonExpression(
            camera_side + [" and '", device, "' != ''"]))

    global_right_if  = _pub_if(global_right_device)
    global_left_if   = _pub_if(global_left_device)
    gripper_right_if = _pub_if(gripper_right_device)
    gripper_left_if  = _pub_if(gripper_left_device)

    # Gripper topics default to '' (absent). If a device is set without a topic,
    # the local publisher and the bridge fall back to the canonical topic name.
    gripper_right_pub_topic = PythonExpression(
        ["'", gripper_right_topic, "' or '/gripper_right_camera/color/image_raw'"])
    gripper_left_pub_topic = PythonExpression(
        ["'", gripper_left_topic, "' or '/gripper_left_camera/color/image_raw'"])

    # Ordered topic list for the camera bridge: global right, global left, then
    # each gripper cam when its topic OR device is set. Track order on the wire
    # (and therefore UI tile order) follows this order.
    camera_topics = PythonExpression([
        "'", global_right_topic, "' + ',' + '", global_left_topic, "'",
        " + ((',' + ('", gripper_right_topic, "' or '/gripper_right_camera/color/image_raw'))"
        " if ('", gripper_right_topic, "' != '' or '", gripper_right_device, "' != '') else '')",
        " + ((',' + ('", gripper_left_topic, "' or '/gripper_left_camera/color/image_raw'))"
        " if ('", gripper_left_topic, "' != '' or '", gripper_left_device, "' != '') else '')",
    ])

    return LaunchDescription([
        DeclareLaunchArgument("side", default_value="both",
                              description="leader | follower | both"),
        DeclareLaunchArgument("ws_url",
                              default_value="https://gt6dof-ui-p55i.onrender.com"),
        DeclareLaunchArgument("session_id", default_value="demo"),
        DeclareLaunchArgument("transport", default_value="ws",
                              description="control-plane transport: ws | zenoh | inproc"),
        DeclareLaunchArgument("zenoh_endpoint", default_value="",
                              description="Zenoh router endpoint, e.g. tcp/router.example.com:7447"),

        # ---- robot selection (default 'none' = pure topic relay) -----------
        DeclareLaunchArgument("leader_arm", default_value="none",
                              description="leader arm driver: 'none' (topic relay) | 'so101' | 'piper'"),
        DeclareLaunchArgument("follower_arm", default_value="none",
                              description="follower arm driver: 'none' (topic relay) | 'so101' | 'piper'"),
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

        # ---- cameras (follower side; on by default) -------------------------
        DeclareLaunchArgument("with_camera", default_value="true",
                              description="launch the cameras + camera_bridge on the follower side"),
        DeclareLaunchArgument("global_right_topic",
                              default_value="/global_right_camera/color/image_raw",
                              description="image topic for the global right view (always streamed)"),
        DeclareLaunchArgument("global_left_topic",
                              default_value="/global_left_camera/color/image_raw",
                              description="image topic for the global left view (always streamed)"),
        DeclareLaunchArgument("gripper_right_topic", default_value="",
                              description="image topic for the gripper right view ('' = no 3rd camera)"),
        DeclareLaunchArgument("gripper_left_topic", default_value="",
                              description="image topic for the gripper left view ('' = no 4th camera)"),
        DeclareLaunchArgument("global_right_device", default_value="",
                              description="global right camera: index ('0') or /dev/v4l/by-id/... path "
                                          "('' = topic published externally, no local publisher)"),
        DeclareLaunchArgument("global_left_device", default_value="",
                              description="global left camera: index or /dev/v4l/by-id/... path "
                                          "('' = topic published externally, no local publisher)"),
        DeclareLaunchArgument("gripper_right_device", default_value="",
                              description="gripper right camera: index or /dev/v4l/by-id/... path "
                                          "('' = no local publisher)"),
        DeclareLaunchArgument("gripper_left_device", default_value="",
                              description="gripper left camera: index or /dev/v4l/by-id/... path "
                                          "('' = no local publisher)"),
        DeclareLaunchArgument("video_transport", default_value="webrtc",
                              description="video transport: webrtc | websocket"),
        DeclareLaunchArgument("video_format", default_value="binary",
                              description="websocket wire format: binary | base64 (ignored for webrtc)"),

        # ---- leader arm drivers ---------------------------------------------
        # SO-101 leader reader -> publishes /leader_joint_states (radians);
        # leader_bridge's input is remapped onto it in robot mode.
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
        # state on 'follower_joint_states' -> remapped to /leader_joint_states.
        # Proper bring-up: enable the arm, then disable it once enable is
        # confirmed so it's back-drivable (disable_after_enable=true).
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

        # ---- bridges (pure relays in every mode) ----------------------------
        Node(
            package="teleop_bridge",
            executable="leader_bridge",
            name="leader_bridge",
            output="screen",
            parameters=[{"transport": transport, "ws_url": ws_url,
                         "zenoh_endpoint": zenoh_ep, "session_id": session,
                         "leader_arm": leader_arm}],
            remappings=[("/joint_cmd", leader_bridge_in)],
            condition=IfCondition(
                PythonExpression(["'", side, on_leader])
            ),
        ),
        Node(
            package="teleop_bridge",
            executable="follower_bridge",
            name="follower_bridge",
            output="screen",
            parameters=[{"transport": transport, "ws_url": ws_url,
                         "zenoh_endpoint": zenoh_ep, "session_id": session,
                         "follower_arm": bridge_follower_arm,
                         "so101_port": follower_port}],
            remappings=[("/joint_cmd", follower_bridge_out)],
            condition=IfCondition(
                PythonExpression(["'", side, on_follower])
            ),
        ),

        # ---- follower arm drivers -------------------------------------------
        # SO-101 follower needs no extra node: follower_bridge drives it
        # directly (FollowerController + serial on so101_port).
        # Piper FOLLOWER: piper_single_ctrl drives the arm over CAN from
        # joint_ctrl_single -> remapped to /follower_joint_commands (what the
        # bridge publishes in robot mode). auto_enable on. Namespaced + its own
        # CAN port so a second arm (can1) can share the machine with the
        # leader (can0).
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

        # ---- cameras (follower/robot side) -----------------------------------
        # device accepts an index or a stable /dev/v4l/by-id|by-path/... symlink
        # so the index can't shuffle.
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="global_right_camera",
            output="screen",
            parameters=[{
                # ParameterValue(str): a numeric index like '2' must reach the
                # node as a string, not a yaml-parsed int.
                "device": ParameterValue(global_right_device, value_type=str),
                "topic": global_right_topic,
                "frame_id": "global_right_camera",
                "width": 640, "height": 480, "fps": 30.0,
            }],
            condition=global_right_if,
        ),
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="global_left_camera",
            output="screen",
            parameters=[{
                "device": ParameterValue(global_left_device, value_type=str),
                "topic": global_left_topic,
                "frame_id": "global_left_camera",
                "width": 640, "height": 480, "fps": 30.0,
            }],
            condition=global_left_if,
        ),
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="gripper_right_camera",
            output="screen",
            parameters=[{
                "device": ParameterValue(gripper_right_device, value_type=str),
                "topic": gripper_right_pub_topic,
                "frame_id": "gripper_right_camera",
                "width": 640, "height": 480, "fps": 30.0,
            }],
            condition=gripper_right_if,
        ),
        Node(
            package="teleop_bridge",
            executable="camera_publisher",
            name="gripper_left_camera",
            output="screen",
            parameters=[{
                "device": ParameterValue(gripper_left_device, value_type=str),
                "topic": gripper_left_pub_topic,
                "frame_id": "gripper_left_camera",
                "width": 640, "height": 480, "fps": 30.0,
            }],
            condition=gripper_left_if,
        ),
        Node(
            package="teleop_bridge",
            executable="camera_bridge",
            name="camera_bridge",
            output="screen",
            parameters=[{
                "ws_url": ws_url,
                "session_id": session,
                "camera_topics": camera_topics,
                "video_transport": video_transport,
                "video_format": video_format,
            }],
            condition=camera_if,
        ),
    ])

from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os

os.environ["RCUTILS_COLORIZED_OUTPUT"] = "1"

# ============================================================
#  Both arms share a single CAN module (can0)
#
#  Left  (Leader/Master)  → sends 0x155/156/157 control cmds
#                         → read via GetArmJointCtrl()
#                         → custom node: piper_read_leader_joint.py
#
#  Right (Servant/Slave)  → sends 0x251/252/2A5/2A6/2A7 feedback
#                         → read via GetArmJointMsgs()
#                         → existing node: piper_single_ctrl
# ============================================================

def generate_launch_description():

    log_level_arg = DeclareLaunchArgument(
        'log_level', default_value='info',
        description='Logging level.'
    )
    can_port_arg = DeclareLaunchArgument(
        'can_port', default_value='can0',
        description='Shared CAN port for both arms.'
    )
    gripper_exist_arg = DeclareLaunchArgument(
        'gripper_exist', default_value='true',
        description='Whether gripper is attached.'
    )
    gripper_val_mutiple_arg = DeclareLaunchArgument(
        'gripper_val_mutiple', default_value='1',
        description='Gripper value multiplier.'
    )

    # ── RIGHT arm (Servant) ──────────────────────────────────
    # Uses existing piper_single_ctrl — reads 2XX feedback IDs
    # Publishes to /joint_states_right
    piper_servant_node = Node(
        package='piper',
        executable='piper_single_ctrl',
        name='piper_servant_node',
        output='screen',
        ros_arguments=['--log-level', LaunchConfiguration('log_level')],
        parameters=[{
            'can_port': LaunchConfiguration('can_port'),
            'auto_enable': True,
            'gripper_exist': LaunchConfiguration('gripper_exist'),
            'gripper_val_mutiple': LaunchConfiguration('gripper_val_mutiple'),
        }],
        remappings=[
            ('joint_states_single', '/joint_states_right'),
            ('joint_states_feedback', '/joint_right'),
            ('joint_ctrl', '/joint_states_ctrl_right'),
            ('arm_status', '/arm_status_right'),
            ('end_pose', '/end_pose_right'),
            ('end_pose_stamped', '/end_pose_stamped_right'),
            ('pos_cmd', '/pos_cmd_right'),
            ('joint_ctrl_single', '/joint_ctrl_cmd_right'),
        ]
    )

    # ── LEFT arm (Leader) ────────────────────────────────────
    # Uses custom node — reads 1XX control command IDs
    # Publishes to /joint_states_left
    piper_leader_node = Node(
        package='piper',
        executable='piper_read_leader_joint',   # add this to setup.py (see README below)
        name='piper_leader_node',
        output='screen',
        ros_arguments=['--log-level', LaunchConfiguration('log_level')],
        parameters=[{
            'can_port': LaunchConfiguration('can_port'),
            'gripper_exist': LaunchConfiguration('gripper_exist'),
        }],
        remappings=[
            ('joint_states', '/joint_states_left'),
        ]
    )

    return LaunchDescription([
        log_level_arg,
        can_port_arg,
        gripper_exist_arg,
        gripper_val_mutiple_arg,
        piper_servant_node,
        piper_leader_node,
    ])
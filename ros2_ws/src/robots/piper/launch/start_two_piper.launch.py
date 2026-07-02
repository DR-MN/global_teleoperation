from launch import LaunchDescription
from launch_ros.actions import Node
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
import os

os.environ["RCUTILS_COLORIZED_OUTPUT"] = "1"   # å¼ºåˆ¶å½©è‰²æ—¥å¿—

def generate_launch_description():
    log_level_arg = DeclareLaunchArgument(
        'log_level',
        default_value='info',
        description='Logging level (debug, info, warn, error, fatal).'
    )
    # Declare the launch arguments
    can_left_port_arg = DeclareLaunchArgument(
        'can_left_port',
        default_value='can0',
        description='CAN left port to be used by the Piper node.'
    )
    can_right_port_arg = DeclareLaunchArgument(
        'can_right_port',
        default_value='can1',
        description='CAN right port to be used by the Piper node.'
    )

    # auto_enable_arg = DeclareLaunchArgument(
    #     'auto_enable',
    #     default_value='false',
    #     description='Automatically enable the Piper node.'
    # )

    auto_enable_left_arg = DeclareLaunchArgument(
        'auto_enable_left',
        default_value='false',
        description='Automatically enable the left Piper arm.'
    )

    auto_enable_right_arg = DeclareLaunchArgument(
        'auto_enable_right',
        default_value='true',
        description='Automatically enable the right Piper arm.'
    )
    
    rviz_ctrl_flag_arg = DeclareLaunchArgument(
        'rviz_ctrl_flag',
        default_value='false',
        description='Start rviz flag.'
    )
    
    girpper_exist_arg = DeclareLaunchArgument(
        'girpper_exist',
        default_value='true',
        description='gripper'
    )

    gripper_val_mutiple_arg = DeclareLaunchArgument(
        'gripper_val_mutiple',
        default_value='1',
        description='gripper'
    )

    # Define the node
    piper_left_node = Node(
        package='piper',
        executable='piper_single_ctrl',
        name='piper_left_ctrl_node',
        #output='screen',
        ros_arguments=['--log-level', LaunchConfiguration('log_level')],
        parameters=[{
            'can_port': LaunchConfiguration('can_left_port'),
            'auto_enable': LaunchConfiguration('auto_enable_left'),
            #'rviz_ctrl_flag': LaunchConfiguration('rviz_ctrl_flag'),
            'girpper_exist': LaunchConfiguration('girpper_exist'),
            'gripper_val_mutiple': LaunchConfiguration('gripper_val_mutiple'),
        }],
        remappings=[
            # æŽ§åˆ¶
            ('pos_cmd', '/pos_cmd_left'),
            ('joint_ctrl_single', '/joint_ctrl_cmd_left'),
            # åé¦ˆ
            ('joint_states_single', '/joint_states_left'),
            ('joint_states_feedback', '/joint_left'),
            ('joint_ctrl', '/joint_states_ctrl_left'),
            ('arm_status', '/arm_status_left'),
            ('end_pose', '/end_pose_left'),
            ('end_pose_stamped', '/end_pose_stamped_left'),
        ]
    )

    piper_right_node = Node(
        package='piper',
        executable='piper_single_ctrl',
        name='piper_right_ctrl_node',
        #output='screen',
        ros_arguments=['--log-level', LaunchConfiguration('log_level')],
        parameters=[{
            'can_port': LaunchConfiguration('can_right_port'),
            'auto_enable': LaunchConfiguration('auto_enable_right'),
            #'rviz_ctrl_flag': LaunchConfiguration('rviz_ctrl_flag'),
            'girpper_exist': LaunchConfiguration('girpper_exist'),
            'gripper_val_mutiple': LaunchConfiguration('gripper_val_mutiple'),
        }],
        remappings=[
            # æŽ§åˆ¶
            ('pos_cmd', '/pos_cmd_right'),
            ('joint_ctrl_single', '/joint_ctrl_cmd_right'),
            # åé¦ˆ
            ('joint_states_single', '/joint_states_right'),
            ('joint_states_feedback', '/joint_right'),
            ('joint_ctrl', '/joint_states_ctrl_right'),
            ('arm_status', '/arm_status_right'),
            ('end_pose', '/end_pose_right'),
            ('end_pose_stamped', '/end_pose_stamped_right'),
        ]
    )

    relay_node = Node(
        package='topic_tools',
        executable='relay',
        name='leader_to_follower_relay',
        arguments=['/joint_states_left', '/joint_ctrl_cmd_right'],
        #output='screen'
    )

    # Return the LaunchDescription
    return LaunchDescription([
        log_level_arg,
        can_left_port_arg,
        can_right_port_arg,
        #auto_enable_arg,
        auto_enable_right_arg,
        auto_enable_left_arg,
        #rviz_ctrl_flag_arg,
        girpper_exist_arg,
        gripper_val_mutiple_arg,
        piper_left_node,
        piper_right_node,
        relay_node,
    ])


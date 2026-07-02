from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    port = LaunchConfiguration('port')
    calibration_dir = LaunchConfiguration('calibration_dir')

    return LaunchDescription([
        DeclareLaunchArgument('port', default_value='/dev/ttyACM1',
                              description='Serial port of the SO-101 leader arm'),
        DeclareLaunchArgument('calibration_dir', default_value='',
                              description='Calibration dir (empty = package .cache)'),
        Node(
            package='so101_ros2',
            executable='so101_ros2_pub_with_conversion',
            name='so101_publisher_node',
            output='screen',
            emulate_tty=True,
            parameters=[
                {'robot_name': 'so101_leader'},
                {'port': port},
                {'recalibrate': False},
                {'calibration_dir': calibration_dir},
            ]
        )
    ])

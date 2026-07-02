import os
from glob import glob
from setuptools import find_packages, setup

package_name = 'so101_ros2'

setup(
    name=package_name,
    version='0.0.0',
    packages=find_packages(include=[package_name, f'{package_name}.*'], exclude=['test']), # This will find so101_ros2 and so101_ros2.lerobot
    # Ship the pre-computed Feetech calibration alongside the device module so a
    # plain (non-symlink) install can still resolve it via __file__/.cache.
    package_data={f'{package_name}.lerobot': ['.cache/*.json']},
    include_package_data=True,
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob(os.path.join('launch', '*_launch.py'))), # Add this line to include your launch files
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='msfshrdc',
    maintainer_email='msfshrdc@todo.todo',
    description='TODO: Package description',
    license='TODO: License declaration',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            # Leader: read SO-101 leader arm -> publish /leader_joint_states (radians).
            'so101_ros2_pub_with_conversion = so101_ros2.so101_ros2_pub_with_conversion:main',
            # Follower (standalone/local mirror only): drive arm from a JointState topic.
            # In the networked teleop path the follower is driven by the teleop_bridge
            # SO101Arm adapter (safety-gated), not by this node.
            'so101_ros2_sub_with_conversion = so101_ros2.so101_ros2_sub_with_conversion:main',
        ],
    },
)

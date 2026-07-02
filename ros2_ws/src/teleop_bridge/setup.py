from setuptools import setup
import os
from glob import glob

package_name = "teleop_bridge"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages",
            ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        (os.path.join("share", package_name, "launch"), glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="teleop",
    maintainer_email="vatbhadaurya@gmail.com",
    description="ROS2 <-> Zenoh bridge for global 6DOF teleoperation.",
    license="Apache-2.0",
    entry_points={
        "console_scripts": [
            "leader_bridge = teleop_bridge.leader_bridge:main",
            "follower_bridge = teleop_bridge.follower_bridge:main",
            "camera_bridge = teleop_bridge.camera_bridge:main",
            "camera_publisher = teleop_bridge.camera_publisher:main",
        ],
    },
)

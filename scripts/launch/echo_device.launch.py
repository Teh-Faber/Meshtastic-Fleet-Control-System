"""
Launch the bridge and the always-on echo device on the remote / moving device.

Run with:
  ros2 launch meshtastic_bridge echo_device.launch.py device_name:=peer
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration


def generate_launch_description():
    share = get_package_share_directory('meshtastic_bridge')
    script = os.path.join(share, 'scripts', 'tests', 'echo_device.py')

    return LaunchDescription([
        DeclareLaunchArgument('serial_port', default_value='auto'),
        DeclareLaunchArgument('device_name', default_value=''),
        DeclareLaunchArgument('start_bridge', default_value='true'),
        # Bridge message size limits; empty keeps bridge_test.yaml (221 B, 4 packets).
        DeclareLaunchArgument('max_payload_bytes', default_value=''),
        DeclareLaunchArgument('max_fragments', default_value=''),
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'bridge.launch.py')),
            launch_arguments={
                'config': os.path.join(share, 'config', 'bridge_test.yaml'),
                'serial_port': LaunchConfiguration('serial_port'),
                'device_name': LaunchConfiguration('device_name'),
                'max_payload_bytes': LaunchConfiguration('max_payload_bytes'),
                'max_fragments': LaunchConfiguration('max_fragments'),
            }.items(),
            condition=IfCondition(LaunchConfiguration('start_bridge')),
        ),
        ExecuteProcess(cmd=['python3', script], output='screen'),
    ])

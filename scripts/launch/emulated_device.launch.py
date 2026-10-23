"""
Launch the bridge and an emulated fleet device publishing typical telemetry.

Run with:
  ros2 launch meshtastic_bridge emulated_device.launch.py device_name:=rover1
  ros2 launch meshtastic_bridge emulated_device.launch.py device_name:=drone1 rx_enabled:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

# Device parameters exposed as launch arguments: name -> default.
DEVICE_ARGS = {
    'publish_period_s': '10.0',
    'lat': '55.3673',
    'lon': '10.4316',
}


def generate_launch_description():
    share = get_package_share_directory('meshtastic_bridge')
    cmd = ['python3', os.path.join(share, 'scripts', 'tests', 'emulated_device.py'),
           '--ros-args']
    for name in DEVICE_ARGS:
        cmd += ['-p', [f'{name}:=', LaunchConfiguration(name)]]

    return LaunchDescription([
        DeclareLaunchArgument('serial_port', default_value='auto'),
        DeclareLaunchArgument('device_name', default_value=''),
        DeclareLaunchArgument('rx_enabled', default_value='true'),
        DeclareLaunchArgument('start_bridge', default_value='true'),
        *[DeclareLaunchArgument(name, default_value=value)
          for name, value in DEVICE_ARGS.items()],
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'bridge.launch.py')),
            launch_arguments={
                'config': os.path.join(share, 'config', 'bridge_test.yaml'),
                'serial_port': LaunchConfiguration('serial_port'),
                'device_name': LaunchConfiguration('device_name'),
                'rx_enabled': LaunchConfiguration('rx_enabled'),
            }.items(),
            condition=IfCondition(LaunchConfiguration('start_bridge')),
        ),
        ExecuteProcess(cmd=cmd, output='screen'),
    ])

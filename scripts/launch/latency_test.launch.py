"""
Launch the bridge and the latency test on the host. The peer runs echo_device.launch.py.

Run with:
  ros2 launch meshtastic_bridge latency_test.launch.py label:=500m count:=50
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription,
                            Shutdown)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

SCRIPT = 'latency_test.py'
# Test parameters exposed as launch arguments: name -> default.
TEST_ARGS = {
    'label': 'unlabeled',
    'count': '50',
    'interval_s': '5.0',
    'timeout_s': '30.0',
    'payload_size': '0',
    'output_dir': '~/fleet_test_results',
}


def generate_launch_description():
    share = get_package_share_directory('meshtastic_bridge')
    cmd = ['python3', os.path.join(share, 'scripts', 'tests', SCRIPT), '--ros-args']
    for name in TEST_ARGS:
        cmd += ['-p', [f'{name}:=', LaunchConfiguration(name)]]

    return LaunchDescription([
        DeclareLaunchArgument('serial_port', default_value='auto'),
        DeclareLaunchArgument('device_name', default_value=''),
        DeclareLaunchArgument('start_bridge', default_value='true'),
        *[DeclareLaunchArgument(name, default_value=value) for name, value in TEST_ARGS.items()],
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'bridge.launch.py')),
            launch_arguments={
                'config': os.path.join(share, 'config', 'bridge_test.yaml'),
                'serial_port': LaunchConfiguration('serial_port'),
                'device_name': LaunchConfiguration('device_name'),
            }.items(),
            condition=IfCondition(LaunchConfiguration('start_bridge')),
        ),
        # Stop the whole launch (bridge included) once the test finishes.
        ExecuteProcess(cmd=cmd, output='screen', on_exit=Shutdown()),
    ])

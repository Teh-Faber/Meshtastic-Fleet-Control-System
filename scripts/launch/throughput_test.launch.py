"""
Launch the bridge and the throughput test on the host. The peer runs echo_device.launch.py.

Run with:
  ros2 launch meshtastic_bridge throughput_test.launch.py label:=500m      (ramp past the limit)
  ros2 launch meshtastic_bridge throughput_test.launch.py interval_s:=0.5  (one run)
  ros2 launch meshtastic_bridge throughput_test.launch.py payload_sizes:=16,200 intervals:=1,0.5
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, ExecuteProcess, IncludeLaunchDescription,
                            Shutdown)
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration

SCRIPT = 'throughput_test.py'
# Test parameters exposed as launch arguments: name -> default.
TEST_ARGS = {
    'label': 'unlabeled',
    # Each run lasts duration_s; count > 0 sends that many messages instead.
    'duration_s': '10.0',
    'count': '0',
    # 0 ramps the rate up until past the link's limit; > 0 is one run at that interval.
    'interval_s': '0.0',
    # 200 B is the largest test message that fits in one bridge packet (221 B).
    'payload_size': '200',
    # Ramp: steps to keep going once goodput stops rising.
    'overload_steps': '2',
    # Comma lists for a sweep over every combination, e.g. 16,64,200.
    'payload_sizes': "''",
    'intervals': "''",
    'pause_s': '2.0',
    'report_timeout_s': '30.0',
    'handshake_timeout_s': '60.0',
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
        ExecuteProcess(cmd=cmd, output='screen', on_exit=Shutdown()),
    ])

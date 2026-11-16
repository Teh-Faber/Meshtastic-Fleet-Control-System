"""
Launch the bridge and the throughput test on the host. The peer runs echo_device.launch.py.

Run with:
  ros2 launch meshtastic_bridge throughput_test.launch.py label:=500m      (ramp up to 800 B/s)
  ros2 launch meshtastic_bridge throughput_test.launch.py max_rate_Bps:=400 ramp_steps:=8
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
    # 0 ramps the rate up to max_rate_Bps; > 0 is one run at that interval.
    'interval_s': '0.0',
    # Ramp: top data rate (payload bytes/s), split into ramp_steps evenly spaced steps.
    'max_rate_Bps': '800.0',
    'ramp_steps': '6',
    # Ramp: top radio packet rate, so small or fragmented messages don't flood the radio.
    'max_packet_rate': '4.0',
    # Test message size. 200 B fits one 221 B bridge packet; up to 848 B with the
    # default 4 fragments (raise max_fragments for more). Larger sizes are refused.
    'payload_size': '200',
    # Ramp: steps to keep going once goodput stops rising (0 = run every step).
    'overload_steps': '2',
    # Comma lists for a sweep over every combination, e.g. 16,64,200.
    'payload_sizes': "''",
    'intervals': "''",
    'pause_s': '2.0',
    'report_timeout_s': '30.0',
    'handshake_timeout_s': '60.0',
    # '' writes to <workspace>/results (or $FLEET_RESULTS_DIR).
    'output_dir': "''",
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
        # Bridge message size limits; empty keeps bridge_test.yaml (221 B, 4 packets).
        DeclareLaunchArgument('max_payload_bytes', default_value=''),
        DeclareLaunchArgument('max_fragments', default_value=''),
        # true: the bridge sends with hop limit 0, so the other radio doesn't rebroadcast.
        DeclareLaunchArgument('direct_only', default_value=''),
        *[DeclareLaunchArgument(name, default_value=value) for name, value in TEST_ARGS.items()],
        IncludeLaunchDescription(
            PythonLaunchDescriptionSource(os.path.join(share, 'launch', 'bridge.launch.py')),
            launch_arguments={
                'config': os.path.join(share, 'config', 'bridge_test.yaml'),
                'serial_port': LaunchConfiguration('serial_port'),
                'device_name': LaunchConfiguration('device_name'),
                'max_payload_bytes': LaunchConfiguration('max_payload_bytes'),
                'max_fragments': LaunchConfiguration('max_fragments'),
                'direct_only': LaunchConfiguration('direct_only'),
            }.items(),
            condition=IfCondition(LaunchConfiguration('start_bridge')),
        ),
        ExecuteProcess(cmd=cmd, output='screen', on_exit=Shutdown()),
    ])

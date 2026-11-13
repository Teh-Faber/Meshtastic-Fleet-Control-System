"""
Launch the Meshtastic bridge.

Launch arguments override the values in the config file. The message size
limits (max_payload_bytes, max_fragments) only do so when given.

Run with:
  ros2 launch meshtastic_bridge bridge.launch.py device_name:=drone1 rx_enabled:=false
  ros2 launch meshtastic_bridge bridge.launch.py max_payload_bytes:=200 max_fragments:=8
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

# Size limits that override the config only when set (name -> type).
OPTIONAL_OVERRIDES = {'max_payload_bytes': int, 'max_fragments': int}


def bridge_node(context):
    overrides = {
        'serial_port': LaunchConfiguration('serial_port').perform(context),
        'device_name': LaunchConfiguration('device_name').perform(context),
        'tx_enabled': LaunchConfiguration('tx_enabled').perform(context).lower() == 'true',
        'rx_enabled': LaunchConfiguration('rx_enabled').perform(context).lower() == 'true',
    }
    for name, cast in OPTIONAL_OVERRIDES.items():
        value = LaunchConfiguration(name).perform(context).strip("'\" ")
        if value:
            overrides[name] = cast(value)
    return [Node(
        package='meshtastic_bridge',
        executable='meshtastic_bridge_node',
        name='meshtastic_bridge_node',
        parameters=[LaunchConfiguration('config').perform(context), overrides],
        output='screen',
    )]


def generate_launch_description():
    share = get_package_share_directory('meshtastic_bridge')
    return LaunchDescription([
        DeclareLaunchArgument(
            'config', default_value=os.path.join(share, 'config', 'meshtastic_bridge_node.yaml')),
        DeclareLaunchArgument('serial_port', default_value='auto'),
        DeclareLaunchArgument(
            'device_name', default_value='',
            description='Name other devices see this one under. Empty: Meshtastic node id'),
        DeclareLaunchArgument(
            'tx_enabled', default_value='true',
            description='Send local topics into the mesh'),
        DeclareLaunchArgument(
            'rx_enabled', default_value='true',
            description='Publish topics received from other devices. false = transmit only'),
        DeclareLaunchArgument(
            'max_payload_bytes', default_value='',
            description='Bridge bytes per LoRa packet. Empty: from the config (221)'),
        DeclareLaunchArgument(
            'max_fragments', default_value='',
            description='Most packets per message. Empty: from the config (4)'),
        OpaqueFunction(function=bridge_node),
    ])

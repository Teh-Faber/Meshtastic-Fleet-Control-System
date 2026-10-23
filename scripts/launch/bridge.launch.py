"""
Launch the Meshtastic bridge.

Launch arguments override the values in the config file.

Run with:
  ros2 launch meshtastic_bridge bridge.launch.py device_name:=drone1 rx_enabled:=false
"""

import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


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
        Node(
            package='meshtastic_bridge',
            executable='meshtastic_bridge_node',
            name='meshtastic_bridge_node',
            parameters=[
                LaunchConfiguration('config'),
                {
                    'serial_port': ParameterValue(
                        LaunchConfiguration('serial_port'), value_type=str),
                    'device_name': ParameterValue(
                        LaunchConfiguration('device_name'), value_type=str),
                    'tx_enabled': ParameterValue(
                        LaunchConfiguration('tx_enabled'), value_type=bool),
                    'rx_enabled': ParameterValue(
                        LaunchConfiguration('rx_enabled'), value_type=bool),
                },
            ],
            output='screen',
        ),
    ])

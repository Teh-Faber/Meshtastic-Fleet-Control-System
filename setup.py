from glob import glob
import os

from setuptools import find_packages, setup

package_name = 'meshtastic_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test', 'scripts']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
        (os.path.join('share', package_name, 'launch'), glob('scripts/launch/*.launch.py')),
        (os.path.join('share', package_name, 'scripts'),
            glob('scripts/*.sh') + glob('scripts/*.py')),
        (os.path.join('share', package_name, 'scripts', 'tests'), glob('scripts/tests/*.py')),
        ('share/' + package_name, ['LICENSE.md']),
    ],
    install_requires=['setuptools', 'pyserial', 'meshtastic'],
    zip_safe=True,
    maintainer='Faber',
    maintainer_email='thoma23@student.sdu.dk',
    description='ROS 2 bridge for decentralized fleet communication over a Meshtastic LoRa mesh',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'meshtastic_bridge_node = meshtastic_bridge.meshtastic_bridge_node:main',
        ],
    },
)

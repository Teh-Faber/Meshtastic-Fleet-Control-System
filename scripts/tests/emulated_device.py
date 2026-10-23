#!/usr/bin/env python3
"""
Emulated fleet device with typical telemetry topics.

Publishes ``<root>/battery_state``, ``<root>/gps/fix`` and ``<root>/status``
the way a real robot would, and logs the same topics arriving from other
devices (``/<device><root>/...``). Use it to check end-to-end integration
and the include/exclude filters, or as a stand-in peer while developing.
"""

import math
import random

from link_test_common import LinkTestNode, run, TopicWatcher
from sensor_msgs.msg import BatteryState, NavSatFix, NavSatStatus
from std_msgs.msg import Header, String


class EmulatedDevice(LinkTestNode):

    def __init__(self):
        super().__init__('emulated_device')
        self.root = self.param('root', '/emulated')
        self.period_s = self.param('publish_period_s', 10.0)
        self.lat = self.param('lat', 55.3673)
        self.lon = self.param('lon', 10.4316)

        self.battery = 1.0
        self.received = {}

        self.battery_pub = self.create_publisher(BatteryState, f'{self.root}/battery_state', 10)
        self.fix_pub = self.create_publisher(NavSatFix, f'{self.root}/gps/fix', 10)
        self.status_pub = self.create_publisher(String, f'{self.root}/status', 10)
        self.create_timer(self.period_s, self.publish)

        self.watchers = [
            TopicWatcher(self, f'{self.root}/battery_state', BatteryState,
                         lambda d, m: self.on_remote(d, 'battery', f'{m.percentage:.0%}')),
            TopicWatcher(self, f'{self.root}/gps/fix', NavSatFix,
                         lambda d, m: self.on_remote(d, 'fix', f'{m.latitude:.5f},'
                                                               f'{m.longitude:.5f}')),
            TopicWatcher(self, f'{self.root}/status', String,
                         lambda d, m: self.on_remote(d, 'status', m.data)),
        ]

    def publish(self):
        now = self.get_clock().now().to_msg()
        self.battery = max(0.0, self.battery - 0.005)
        self.battery_pub.publish(BatteryState(header=self._header(now), percentage=self.battery,
                                              voltage=float('nan'), present=True))

        # Wander a few metres around the configured position.
        self.lat += random.uniform(-2e-5, 2e-5)
        self.lon += random.uniform(-2e-5, 2e-5) / math.cos(math.radians(self.lat))
        fix = NavSatFix(header=self._header(now), latitude=self.lat, longitude=self.lon)
        fix.status.status = NavSatStatus.STATUS_FIX
        self.fix_pub.publish(fix)

        self.status_pub.publish(String(data='ok' if self.battery > 0.2 else 'low_battery'))

    @staticmethod
    def _header(stamp):
        return Header(stamp=stamp, frame_id='base_link')

    def on_remote(self, device, kind, text):
        self.received[device] = self.received.get(device, 0) + 1
        self.get_logger().info(f'[{device}] {kind}: {text}')

    def finish(self):
        self.get_logger().info(f'Messages received per device: {self.received}')


def main(args=None):
    run(EmulatedDevice, args)


if __name__ == '__main__':
    main()

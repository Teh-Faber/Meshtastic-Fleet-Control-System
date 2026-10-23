#!/usr/bin/env python3
"""
Measure delivered data rate to the echo device.

Publishes ``count`` messages of ``payload_size`` bytes on
``/link_test/data`` (std_msgs/UInt8MultiArray) every ``interval_s``, then
asks the echo device how many arrived (``/link_test/report_request``,
repeated until ``/<peer>/link_test/report`` comes back).

The bridge keeps only the latest message per topic, so messages published
faster than the radio can send are overwritten and count as lost. Finding
that point is the goal of this test; sweep it with
scripts/sweep_throughput.sh. Mind the EU868 duty-cycle limits.

Appends a one-line result to ``throughput_summary.csv``.
"""

import json
import time

from link_test_common import (append_summary, DATA, DATA_HEADER, HostTest, REPORT,
                              REPORT_REQUEST, run, TopicWatcher)
from std_msgs.msg import String, UInt8MultiArray


class ThroughputTest(HostTest):

    def __init__(self):
        super().__init__('throughput_test')
        self.count = self.param('count', 50)
        self.interval_s = self.param('interval_s', 2.0)
        self.payload_size = max(DATA_HEADER.size, self.param('payload_size', 200))
        self.report_timeout_s = self.param('report_timeout_s', 60.0)

        # Created before the handshake so the bridge has already announced
        # these topics when the measured part starts.
        self.data_pub = self.create_publisher(UInt8MultiArray, DATA, 10)
        self.request_pub = self.create_publisher(String, REPORT_REQUEST, 10)
        self.report_watcher = TopicWatcher(self, REPORT, String, self.on_report)

        self.seq = 0
        self.t_start = None
        self.t_end = None
        self.report = None

    def start(self):
        self.t_start = time.monotonic()
        self.create_timer(self.interval_s, self.tick)
        self.get_logger().info(
            f'Throughput: {self.count} x {self.payload_size} B every {self.interval_s}s '
            f'(offered {self.payload_size / self.interval_s:.1f} B/s)')

    def tick(self):
        if self.seq < self.count:
            header = DATA_HEADER.pack(self.run_id, self.seq, self.count)
            payload = header + bytes(self.payload_size - len(header))
            self.data_pub.publish(UInt8MultiArray(data=list(payload)))
            self.seq += 1
            return
        if self.t_end is None:
            self.t_end = time.monotonic()
            self.create_timer(3.0, self.request_report)
            self.get_logger().info('All data sent, requesting report')

    def request_report(self):
        if self.report is not None:
            return
        if time.monotonic() - self.t_end > self.report_timeout_s:
            self.get_logger().error('No report from the echo device')
            self.done = True
            return
        self.request_pub.publish(String(data=str(self.run_id)))

    def on_report(self, device, msg):
        try:
            report = json.loads(msg.data)
        except ValueError:
            return
        if report.get('run_id') != self.run_id or self.report is not None:
            return
        self.report = report
        self.done = True

    def finish(self):
        if self.t_start is None:
            return  # handshake never completed
        report = self.report or {}
        received = report.get('received')
        span = report.get('span_s') or 0.0
        goodput = report.get('bytes', 0) / span if span > 0 else None
        row = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'label': self.label,
            'peer': self.peer,
            'payload_size': self.payload_size,
            'interval_s': self.interval_s,
            'offered_Bps': round(self.payload_size / self.interval_s, 1),
            'sent': self.seq,
            'received': received,
            'pdr': round(received / self.seq, 3) if received is not None and self.seq else None,
            'goodput_Bps': round(goodput, 1) if goodput else None,
            # Link quality as measured by the echo device (host -> echo).
            'rssi_mean': report.get('rssi_mean'),
            'snr_mean': report.get('snr_mean'),
            'hops': report.get('hops'),
        }
        path = append_summary(self.output_dir, 'throughput', row)
        self.get_logger().info(
            f'Delivered {received}/{self.seq}, goodput {row["goodput_Bps"]} B/s, '
            f'rssi {row["rssi_mean"]} snr {row["snr_mean"]}. Summary in {path}')


def main(args=None):
    run(ThroughputTest, args)


if __name__ == '__main__':
    main()

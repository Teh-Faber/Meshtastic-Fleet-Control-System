#!/usr/bin/env python3
"""
Always-on test peer for the remote / moving device.

Start it once and leave it running; the host can run any number of latency
and throughput tests against it without touching this device.

- Pings (``/<host>/link_test/ping``) are republished unchanged on
  ``/link_test/pong``.
- Throughput data (``/<host>/link_test/data``) is counted per host and run,
  together with the RSSI/SNR it arrived with.
- A report request (``/<host>/link_test/report_request``) is answered with
  that run's counts as JSON on ``/link_test/report``.
"""

import json
import time

from link_test_common import (DATA, DATA_HEADER, LinkQuality, LinkTestNode, PING, PONG, REPORT,
                              REPORT_REQUEST, run, summarize_link, TopicWatcher)
from std_msgs.msg import Header, String, UInt8MultiArray

# Throughput runs kept in memory for report requests.
MAX_RUNS = 20


class EchoDevice(LinkTestNode):

    def __init__(self):
        super().__init__('echo_device')
        self.link = LinkQuality(self, self.param('link_quality_topic', '/mesh/link_quality'))
        self.pong_pub = self.create_publisher(Header, PONG, 10)
        self.report_pub = self.create_publisher(String, REPORT, 10)
        self.runs = {}  # (host device, run id) -> counters
        self.pongs = 0

        self.watchers = [
            TopicWatcher(self, PING, Header, self.on_ping),
            TopicWatcher(self, DATA, UInt8MultiArray, self.on_data),
            TopicWatcher(self, REPORT_REQUEST, String, self.on_report_request),
        ]
        self.get_logger().info('Echo device ready')

    def on_ping(self, device, msg):
        self.pong_pub.publish(msg)
        self.pongs += 1

    def on_data(self, device, msg):
        data = bytes(msg.data)
        if len(data) < DATA_HEADER.size:
            return
        run_id, seq, count = DATA_HEADER.unpack_from(data)
        key = (device, run_id)
        if key not in self.runs:
            self.get_logger().info(f'Throughput run {run_id} from {device} ({count} messages)')
            if len(self.runs) >= MAX_RUNS:
                del self.runs[next(iter(self.runs))]
            now = time.monotonic()
            self.runs[key] = {'count': count, 'seqs': set(), 'bytes': 0,
                              't_first': now, 't_last': now, 'link': []}
        stats = self.runs[key]
        stats['seqs'].add(seq)
        stats['bytes'] += len(data)
        stats['t_last'] = time.monotonic()
        stats['link'].append(self.link.get(device, DATA))

    def on_report_request(self, device, msg):
        try:
            run_id = int(msg.data)
        except ValueError:
            return
        stats = self.runs.get((device, run_id))
        report = {'run_id': run_id, 'received': 0}
        if stats:
            report.update({
                'received': len(stats['seqs']),
                'count': stats['count'],
                'bytes': stats['bytes'],
                'span_s': round(stats['t_last'] - stats['t_first'], 3),
                **summarize_link(stats['link']),
            })
        # Answered every time it is asked; the host repeats the request
        # until a report gets through.
        self.report_pub.publish(String(data=json.dumps(report)))

    def finish(self):
        self.get_logger().info(f'Sent {self.pongs} pongs, served {len(self.runs)} throughput runs')


def main(args=None):
    run(EchoDevice, args)


if __name__ == '__main__':
    main()

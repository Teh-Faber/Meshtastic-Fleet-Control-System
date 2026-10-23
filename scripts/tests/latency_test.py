#!/usr/bin/env python3
"""
Measure round-trip latency to the echo device.

Publishes numbered pings on ``/link_test/ping`` every ``interval_s`` and
times the pongs coming back as ``/<peer>/link_test/pong``. The RTT covers
the full path an application sees: ROS -> bridge -> mesh -> bridge -> ROS
and back. All timing uses this host's clock, so no clock sync is needed.

Writes one row per ping to ``latency_<time>.csv`` (with the RSSI/SNR/hops
of each pong) and appends a one-line result to ``latency_summary.csv``.
"""

import time

from link_test_common import (append_summary, CsvLog, format_summary, HostTest, PONG, run,
                              summarize)


class LatencyTest(HostTest):

    def __init__(self):
        super().__init__('latency_test')
        self.count = self.param('count', 50)
        self.interval_s = self.param('interval_s', 5.0)
        self.timeout_s = self.param('timeout_s', 30.0)
        # Pads the ping's frame_id to this many characters (0 = no padding).
        self.payload_size = self.param('payload_size', 0)

        self.seq = 0
        self.pending = {}  # seq -> monotonic send time
        self.rtts = []
        self.lost = 0
        self.log = None

    def start(self):
        self.log = CsvLog(self.output_dir, f'latency_{self.label}' if self.label else 'latency',
                          ['seq', 't', 'status', 'rtt_ms', 'rssi', 'snr', 'hops'])
        self.create_timer(self.interval_s, self.tick)
        self.get_logger().info(f'Latency: {self.count} pings every {self.interval_s}s')

    def tick(self):
        now = time.monotonic()
        for seq, t_sent in list(self.pending.items()):
            if now - t_sent > self.timeout_s:
                del self.pending[seq]
                self.lost += 1
                self.log.write(seq=seq, t=time.time(), status='timeout')
        if self.seq < self.count:
            self.pending[self.seq] = now
            self.publish_ping(str(self.seq), self.payload_size)
            self.seq += 1
        elif not self.pending:
            self.done = True

    def on_pong(self, device, seq):
        t_sent = self.pending.pop(seq, None)
        if t_sent is None:
            return  # late, already counted as lost
        rtt_ms = (time.monotonic() - t_sent) * 1000.0
        self.rtts.append(rtt_ms)
        link = self.link.get(device, PONG)
        self.log.write(seq=seq, t=time.time(), status='ok', rtt_ms=round(rtt_ms, 1),
                       rssi=link.get('rssi'), snr=link.get('snr'), hops=link.get('hops'))

    def finish(self):
        if self.log is None:
            return  # handshake never completed
        self.log.close()
        sent = len(self.rtts) + self.lost
        stats = summarize(self.rtts)
        self.get_logger().info(format_summary('RTT', stats, ' ms'))
        self.get_logger().info(f'Answered {len(self.rtts)}/{sent}')
        path = append_summary(self.output_dir, 'latency', {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'label': self.label,
            'peer': self.peer,
            'payload_size': self.payload_size,
            'interval_s': self.interval_s,
            'sent': sent,
            'answered': len(self.rtts),
            'pdr': round(len(self.rtts) / sent, 3) if sent else None,
            'rtt_mean_ms': round(stats['mean'], 1) if self.rtts else None,
            'rtt_median_ms': round(stats['median'], 1) if self.rtts else None,
            'rtt_p95_ms': round(stats['p95'], 1) if self.rtts else None,
            'rtt_max_ms': round(stats['max'], 1) if self.rtts else None,
            **self.link_summary(PONG),
        })
        self.get_logger().info(f'Results in {self.log.path}, summary in {path}')


def main(args=None):
    run(LatencyTest, args)


if __name__ == '__main__':
    main()

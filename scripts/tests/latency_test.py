#!/usr/bin/env python3
"""
Measure round-trip latency to the echo device.

Publishes numbered pings on ``/link_test/ping`` every ``interval_s`` and
times the pongs coming back as ``/<peer>/link_test/pong``. The RTT covers
the full path an application sees: ROS -> bridge -> mesh -> bridge -> ROS
and back. All timing uses this host's clock, so no clock sync is needed.

Writes one row per ping to ``latency_<time>.csv`` (with the RSSI/SNR/hops
of each pong and, with GPS on both T-Beams, both positions and their
distance) and appends a one-line result to ``latency_summary.csv``.
"""

import time

from link_test_common import (append_summary, CsvLog, format_summary, HostTest, PONG, run,
                              summarize)

POSITION_COLUMNS = ['host_lat', 'host_lon', 'peer_lat', 'peer_lon', 'distance_m']


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
        self.distances = []
        self.log = None

    def start(self):
        self.log = CsvLog(self.output_dir, f'latency_{self.label}' if self.label else 'latency',
                          ['seq', 't', 'status', 'rtt_ms', 'rssi', 'snr', 'hops',
                           *POSITION_COLUMNS])
        self.create_timer(self.interval_s, self.tick)
        self.get_logger().info(f'Latency: {self.count} pings every {self.interval_s}s')

    def tick(self):
        now = time.monotonic()
        for seq, t_sent in list(self.pending.items()):
            if now - t_sent > self.timeout_s:
                del self.pending[seq]
                self.lost += 1
                self.log.write(seq=seq, t=time.time(), status='timeout',
                               **self.position_fields())
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
        position = self.position_fields()
        if position['distance_m'] is not None:
            self.distances.append(position['distance_m'])
        self.log.write(seq=seq, t=time.time(), status='ok', rtt_ms=round(rtt_ms, 1),
                       rssi=link.get('rssi'), snr=link.get('snr'), hops=link.get('hops'),
                       **position)

    def finish(self):
        if self.log is None:
            return  # handshake never completed
        self.log.close()
        sent = len(self.rtts) + self.lost
        stats = summarize(self.rtts)
        self.get_logger().info(format_summary('RTT', stats, ' ms'))
        self.get_logger().info(f'Answered {len(self.rtts)}/{sent}')
        distance = summarize(self.distances)
        if self.distances:
            self.get_logger().info(format_summary('Distance', distance, ' m'))
        else:
            self.get_logger().info('Distance: no GPS fix on both ends')
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
            # Median over the answered pings; the ends may move during a run.
            'distance_m': round(distance['median'], 1) if self.distances else None,
            'distance_min_m': round(distance['min'], 1) if self.distances else None,
            'distance_max_m': round(distance['max'], 1) if self.distances else None,
            **{k: v for k, v in self.position_fields().items() if k != 'distance_m'},
        })
        self.get_logger().info(f'Results in {self.log.path}, summary in {path}')


def main(args=None):
    run(LatencyTest, args)


if __name__ == '__main__':
    main()

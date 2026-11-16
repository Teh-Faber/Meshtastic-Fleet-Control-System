#!/usr/bin/env python3
"""
Measure round-trip latency to the echo device.

Publishes numbered pings on ``/link_test/ping`` and times the pongs coming
back as ``/<peer>/link_test/pong``. The RTT covers the full path an
application sees: ROS -> bridge -> mesh -> bridge -> ROS and back. All
timing uses this host's clock, so no clock sync is needed.

One ping is in flight at a time: the next one goes out as soon as the
previous one is answered or has timed out, but never sooner than
``interval_s`` after the previous ping. A run therefore takes about
``count`` x RTT instead of a fixed schedule, and pings never queue up behind
each other in the radio, which would inflate the RTT.

The timeout adapts to the link: ``TIMEOUT_RTT_FACTOR`` x the median RTT so
far, at least ``MIN_TIMEOUT_S`` and at most ``timeout_s`` (also used until
the first answer). A lost ping then costs a few RTTs, not ``timeout_s``.
Pongs arriving after their timeout count as lost and are logged as late.

Logs one line per ping. Writes one row per ping to ``latency_<time>.csv``
(with the RSSI/SNR/hops of each pong and, with GPS on both T-Beams, both
positions and their distance) and appends a one-line result to
``latency_summary.csv``.
"""

import statistics
import time

from link_test_common import (append_summary, CsvLog, format_duration, format_summary,
                              HostTest, PONG, run, summarize)

POSITION_COLUMNS = ['host_lat', 'host_lon', 'peer_lat', 'peer_lon', 'distance_m']
TIMEOUT_RTT_FACTOR = 4.0
MIN_TIMEOUT_S = 2.0


class LatencyTest(HostTest):

    def __init__(self):
        super().__init__('latency_test')
        self.count = self.param('count', 20)
        # Minimum time between two pings.
        self.interval_s = self.param('interval_s', 1.0)
        # Upper limit of the adaptive timeout.
        self.timeout_s = self.param('timeout_s', 10.0)
        # Pads the ping's frame_id to this many characters (0 = no padding).
        self.payload_size = self.param('payload_size', 0)

        self.seq = 0
        self.pending = None  # (seq, monotonic send time) of the ping in flight
        self.sent_at = {}    # seq -> monotonic send time, to report late pongs
        self.t_last_ping = 0.0
        self.t_start = None
        self.rtts = []
        self.lost = 0
        self.distances = []
        self.log = None

    def start(self):
        self.log = CsvLog(self.output_dir, f'latency_{self.label}' if self.label else 'latency',
                          ['seq', 't', 'status', 'rtt_ms', 'rssi', 'snr', 'hops',
                           *POSITION_COLUMNS])
        self.t_start = time.monotonic()
        self.create_timer(0.05, self.tick)
        self.get_logger().info(
            f'Latency: {self.count} pings, one at a time (at most every {self.interval_s}s, '
            f'timeout {TIMEOUT_RTT_FACTOR:g} x RTT, {MIN_TIMEOUT_S:g}-{self.timeout_s:g}s)')

    def current_timeout(self):
        if not self.rtts:
            return self.timeout_s
        adaptive = TIMEOUT_RTT_FACTOR * statistics.median(self.rtts) / 1000.0
        return min(self.timeout_s, max(MIN_TIMEOUT_S, adaptive))

    def tick(self):
        now = time.monotonic()
        if self.pending is not None:
            seq, t_sent = self.pending
            timeout = self.current_timeout()
            if now - t_sent < timeout:
                return
            self.pending = None
            self.lost += 1
            self.log.write(seq=seq, t=time.time(), status='timeout', **self.position_fields())
            self.get_logger().warn(
                f'{self.progress()} ping {seq}: no answer within {timeout:.1f}s')
        if self.seq >= self.count:
            self.done = True
            return
        if now - self.t_last_ping < self.interval_s:
            return
        self.pending = (self.seq, now)
        self.sent_at[self.seq] = now
        self.t_last_ping = now
        self.publish_ping(str(self.seq), self.payload_size)
        self.seq += 1

    def on_pong(self, device, seq):
        if self.pending is None or self.pending[0] != seq:
            t_sent = self.sent_at.pop(seq, None)
            if t_sent is not None:
                self.get_logger().info(
                    f'Ping {seq} answered late, after {(time.monotonic() - t_sent) * 1000:.0f} ms '
                    '(counted as lost)')
            return
        rtt_ms = (time.monotonic() - self.pending[1]) * 1000.0
        self.pending = None
        self.sent_at.pop(seq, None)
        self.rtts.append(rtt_ms)
        link = self.link.get(device, PONG)
        position = self.position_fields()
        if position['distance_m'] is not None:
            self.distances.append(position['distance_m'])
        self.log.write(seq=seq, t=time.time(), status='ok', rtt_ms=round(rtt_ms, 1),
                       rssi=link.get('rssi'), snr=link.get('snr'), hops=link.get('hops'),
                       **position)
        details = [f'RSSI {link["rssi"]} dBm' if link.get('rssi') else '',
                   f'SNR {link["snr"]} dB' if link.get('snr') else '',
                   f'{link["hops"]} hops' if link.get('hops') else '',
                   f'{position["distance_m"]:.0f} m' if position['distance_m'] is not None
                   else '']
        details = ', '.join(d for d in details if d)
        suffix = f' ({details})' if details else ''
        self.get_logger().info(f'{self.progress()} ping {seq}: {rtt_ms:.0f} ms{suffix}')

    def progress(self):
        """``[done/count]`` plus the expected time left, from the RTTs so far."""
        done = len(self.rtts) + self.lost
        left = self.count - done
        if left and self.rtts:
            per_ping = max(self.interval_s, sum(self.rtts) / len(self.rtts) / 1000.0)
            return f'[{done}/{self.count}, ~{format_duration(left * per_ping)} left]'
        return f'[{done}/{self.count}]'

    def finish(self):
        if self.log is None:
            return  # handshake never completed
        self.log.close()
        sent = len(self.rtts) + self.lost
        stats = summarize(self.rtts)
        log = self.get_logger()
        log.info(f'Latency done in {format_duration(time.monotonic() - self.t_start)}: '
                 f'answered {len(self.rtts)}/{sent}')
        log.info(format_summary('RTT', stats, ' ms'))
        distance = summarize(self.distances)
        if self.distances:
            log.info(format_summary('Distance', distance, ' m'))
        else:
            log.info('Distance: no GPS fix on both ends')
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
        log.info(f'Results in {self.log.path}, summary in {path}')


def main(args=None):
    run(LatencyTest, args)


if __name__ == '__main__':
    main()

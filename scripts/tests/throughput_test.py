#!/usr/bin/env python3
"""
Measure delivered data rate to the echo device, up to and past the link's limit.

Each run publishes messages of a given size on ``/link_test/data``
(std_msgs/UInt8MultiArray) at a fixed interval for ``duration_s`` (or
``count`` messages), then asks the echo device how many arrived
(``/link_test/report_request``, repeated until ``/<peer>/link_test/report``
comes back).

Modes:

- **Ramp** (default, ``interval_s:=0``): starts slow and shortens the
  interval step by step (``RAMP_INTERVALS``) until the offered load is well
  past what the radio can carry. Once goodput stops rising it does
  ``overload_steps`` more steps to show the overload, then stops, and logs
  the maximum goodput.
- **Single run**: ``interval_s:=0.5`` sends one run at that interval.
- **Sweep**: ``payload_sizes`` and ``intervals`` (comma separated, e.g.
  ``16,64,210``) run every combination, without stopping early.

All runs share one bridge connection, so the bridge start and the handshake
happen once.

Why runs last ``duration_s`` rather than a few messages: the radio buffers
about 16 packets, so a short burst is absorbed by the queue and still
arrives in full after the burst. Runs long enough to overflow it show the
real limit: delivery drops and goodput levels off at what the link carries.
When the radio's queue is full the bridge blocks until a slot frees up, and
messages published meanwhile are overwritten and count as lost.

The default size of 210 B is the largest that still fits in one LoRa packet
(larger messages are split into fragments, and losing one loses the message).

Logs progress while sending and one result line per run. Appends one row
per run to ``throughput_summary.csv``, with both GPS positions and their
distance when the T-Beams have a fix (the echo device's position comes with
its report). Mind the EU868 duty-cycle limits: runs past the limit keep the
radio transmitting the whole time.
"""

import json
import math
import time

from link_test_common import (append_summary, DATA, DATA_HEADER, format_duration, HostTest,
                              parse_list, REPORT, REPORT_REQUEST, run, TopicWatcher)
from std_msgs.msg import String, UInt8MultiArray

# Ramp steps (s between messages). On SHORT_FAST a 210 B message is ~0.19 s
# on air, so the radio saturates around 0.2 s and 0.05 s offers ~4x its
# capacity. On LONG_FAST (~2 s per message) it saturates at the first step.
RAMP_INTERVALS = [1.0, 0.5, 0.3, 0.2, 0.15, 0.1, 0.05]
# Goodput must rise by more than this to count as "still rising".
RAMP_GAIN = 1.05
# Fewest messages per run, whatever duration_s says.
MIN_COUNT = 10
# Wait this long after the last message before the first report request.
REPORT_DELAY_S = 1.0
REPORT_RETRY_S = 2.0
# Log sending progress at most this often.
PROGRESS_PERIOD_S = 5.0


class ThroughputTest(HostTest):

    def __init__(self):
        super().__init__('throughput_test')
        # 0: derive each run's count from duration_s.
        self.fixed_count = self.param('count', 0)
        self.duration_s = self.param('duration_s', 10.0)
        sizes = parse_list(self.param('payload_sizes', ''), int) or \
            [self.param('payload_size', 210)]
        intervals = parse_list(self.param('intervals', ''), float)
        interval_s = self.param('interval_s', 0.0)
        if intervals:
            self.mode = 'sweep'
        elif interval_s > 0:
            self.mode = 'single'
            intervals = [interval_s]
        else:
            self.mode = 'ramp'
            intervals = RAMP_INTERVALS
        self.overload_steps = self.param('overload_steps', 2)
        self.report_timeout_s = self.param('report_timeout_s', 30.0)
        # Lets the radio's transmit queue drain between runs.
        self.pause_s = self.param('pause_s', 2.0)
        self.runs = [(max(DATA_HEADER.size, size), interval)
                     for size in sizes for interval in intervals]

        # Created before the handshake so the bridge has already announced
        # these topics when the measured part starts.
        self.data_pub = self.create_publisher(UInt8MultiArray, DATA, 10)
        self.request_pub = self.create_publisher(String, REPORT_REQUEST, 10)
        self.report_watcher = TopicWatcher(self, REPORT, String, self.on_report)

        self.index = -1        # current run
        self.state = None      # 'send', 'report', 'pause'
        self.state_since = 0.0
        self.send_timer = None
        self.seq = 0
        self.count = 0
        self.report = None
        self.t_start = None
        self.t_last_progress = 0.0
        self.t_next_request = 0.0
        self.summary_path = None
        self.results = []      # summary rows of the finished runs
        self.ramp = {}         # payload size -> {'best': row, 'beyond': n, 'done': bool}

    # --- run sequencing --------------------------------------------------

    @property
    def run_id_now(self):
        # Every run gets its own id; the echo device keeps counts per id.
        return (self.run_id + 1 + self.index) & 0xFFFF

    def run_count(self, interval):
        if self.fixed_count > 0:
            return self.fixed_count
        return max(MIN_COUNT, math.ceil(self.duration_s / interval))

    def start(self):
        self.t_start = time.monotonic()
        send_s = sum(self.run_count(interval) * interval for _, interval in self.runs)
        overhead_s = len(self.runs) * (REPORT_DELAY_S + 1.0 + self.pause_s)
        what = {'ramp': 'ramp to past the limit, stops early once saturated',
                'single': 'single run', 'sweep': 'sweep'}[self.mode]
        self.get_logger().info(
            f'Throughput ({what}): up to {len(self.runs)} run(s), '
            f'at most {format_duration(send_s + overhead_s)}')
        self.create_timer(0.1, self.control)
        self.next_run()

    def next_run(self):
        self.index += 1
        # A finished ramp skips the rest of its steps.
        while (self.index < len(self.runs)
               and self.ramp.get(self.runs[self.index][0], {}).get('done')):
            self.index += 1
        if self.index >= len(self.runs):
            self.done = True
            return
        size, interval = self.runs[self.index]
        self.count = self.run_count(interval)
        self.seq = 0
        self.report = None
        self.set_state('send')
        self.t_last_progress = time.monotonic()
        self.get_logger().info(
            f'{self.run_label()}: {self.count} x {size} B every {interval}s '
            f'(offered {size / interval:.0f} B/s, ~{format_duration(self.count * interval)})')
        self.send_timer = self.create_timer(interval, self.send_one)

    def set_state(self, state):
        self.state = state
        self.state_since = time.monotonic()

    def run_label(self):
        return f'Run {self.index + 1}/{len(self.runs)}'

    def send_one(self):
        size, _ = self.runs[self.index]
        header = DATA_HEADER.pack(self.run_id_now, self.seq, self.count)
        payload = header + bytes(size - len(header))
        self.data_pub.publish(UInt8MultiArray(data=list(payload)))
        self.seq += 1
        now = time.monotonic()
        if self.seq >= self.count:
            self.destroy_timer(self.send_timer)
            self.send_timer = None
            self.set_state('report')
            self.t_next_request = now + REPORT_DELAY_S
            self.get_logger().info(f'{self.run_label()}: all sent, asking the echo device')
        elif now - self.t_last_progress >= PROGRESS_PERIOD_S:
            self.t_last_progress = now
            self.get_logger().info(f'{self.run_label()}: sent {self.seq}/{self.count}')

    def control(self):
        now = time.monotonic()
        if self.state == 'report':
            if self.report is not None:
                self.finish_run('ok')
            elif now - self.state_since > self.report_timeout_s:
                self.get_logger().error(
                    f'{self.run_label()}: no report from the echo device within '
                    f'{self.report_timeout_s:.0f}s')
                self.finish_run('no_report')
            elif now >= self.t_next_request:
                self.request_pub.publish(String(data=str(self.run_id_now)))
                self.t_next_request = now + REPORT_RETRY_S
        elif self.state == 'pause' and now - self.state_since >= self.pause_s:
            self.next_run()

    def on_report(self, device, msg):
        try:
            report = json.loads(msg.data)
        except ValueError:
            return
        if self.state == 'report' and report.get('run_id') == self.run_id_now:
            self.report = report

    # --- results -----------------------------------------------------------

    def finish_run(self, status):
        row = self.write_row(status)
        self.results.append(row)
        if row['received'] is None:
            delivered = 'no report'
        else:
            delivered = f'delivered {row["received"]}/{row["sent"]} ({row["pdr"]:.0%})'
        details = [f'goodput {row["goodput_Bps"]} B/s' if row['goodput_Bps'] else '',
                   f'RSSI {row["rssi_mean"]} dBm' if row['rssi_mean'] is not None else '',
                   f'SNR {row["snr_mean"]} dB' if row['snr_mean'] is not None else '',
                   f'{row["distance_m"]:.0f} m' if row['distance_m'] is not None else '']
        self.get_logger().info(
            f'{self.run_label()}: {delivered}' + ''.join(f', {d}' for d in details if d))
        if self.mode == 'ramp':
            self.update_ramp(row)
        self.set_state('pause')
        if self.index + 1 >= len(self.runs):
            self.next_run()  # last run: no pause

    def update_ramp(self, row):
        """Track the best goodput per size; stop a size's ramp once past the limit."""
        size = row['payload_size']
        ramp = self.ramp.setdefault(size, {'best': None, 'beyond': 0, 'done': False})
        goodput = row['goodput_Bps'] or 0.0
        best = ramp['best']
        best_goodput = (best['goodput_Bps'] or 0.0) if best else 0.0
        if best is None or goodput > best_goodput * RAMP_GAIN:
            ramp['best'], ramp['beyond'] = row, 0
            return
        if goodput > best_goodput:
            ramp['best'] = row  # slightly higher, but no longer rising
        ramp['beyond'] += 1
        if ramp['beyond'] == 1:
            self.get_logger().info(
                f'Goodput stopped rising: link saturated at about '
                f'{ramp["best"]["goodput_Bps"]} B/s')
        if ramp['beyond'] >= self.overload_steps:
            ramp['done'] = True
            self.get_logger().info(
                f'{self.overload_steps} step(s) past the limit, ending the ramp')

    def write_row(self, status):
        size, interval = self.runs[self.index]
        report = self.report or {}
        received = report.get('received')
        span = report.get('span_s') or 0.0
        # span runs from the first to the last arrival, i.e. covers n - 1
        # messages' worth of time: leave the first message's bytes out.
        goodput = None
        if span > 0 and received and received > 1:
            goodput = report.get('bytes', 0) * (received - 1) / received / span
        row = {
            'time': time.strftime('%Y-%m-%d %H:%M:%S'),
            'label': self.label,
            'peer': self.peer,
            'mode': self.mode,
            'payload_size': size,
            'interval_s': interval,
            'offered_Bps': round(size / interval, 1),
            'sent': self.seq,
            'received': received,
            'pdr': round(received / self.seq, 3) if received is not None and self.seq else None,
            'goodput_Bps': round(goodput, 1) if goodput else None,
            'delivered_msg_s': round(goodput / size, 2) if goodput else None,
            # Link quality as measured by the echo device (host -> echo).
            'rssi_mean': report.get('rssi_mean'),
            'snr_mean': report.get('snr_mean'),
            'hops': report.get('hops'),
            **self.position_fields(report.get('position')),
            'status': status,
        }
        self.summary_path = append_summary(self.output_dir, 'throughput', row)
        return row

    def finish(self):
        if self.t_start is None:
            return  # handshake never completed
        if self.state in ('send', 'report') and self.index < len(self.runs):
            self.write_row('interrupted')
        log = self.get_logger()
        log.info(f'Throughput done: {len(self.results)} run(s) in '
                 f'{format_duration(time.monotonic() - self.t_start)}')
        if len(self.results) > 1:
            log.info('  size  interval   offered  delivered        goodput')
            for row in self.results:
                pdr = f'{row["pdr"]:.0%}' if row['pdr'] is not None else '-'
                delivered = (f'{row["received"]}/{row["sent"]} ({pdr})'
                             if row['received'] is not None else 'no report')
                log.info(f'  {row["payload_size"]:>4} B  {row["interval_s"]:>6}s  '
                         f'{row["offered_Bps"]:>6.0f} B/s  {delivered:<15}  '
                         f'{row["goodput_Bps"] or "-"} B/s')
        for size, ramp in self.ramp.items():
            best = ramp['best']
            if best is None:
                continue
            if ramp['beyond']:
                log.info(f'Max goodput ({size} B messages): {best["goodput_Bps"]} B/s '
                         f'= {best["delivered_msg_s"]} msg/s, reached at {best["interval_s"]}s '
                         'intervals; faster sending only loses more messages')
            else:
                log.info(f'Goodput ({size} B messages) still rising at the fastest step: '
                         f'{best["goodput_Bps"]} B/s. The limit is above what the ramp offers')
        if self.summary_path:
            log.info(f'Summary in {self.summary_path}')


def main(args=None):
    run(ThroughputTest, args)


if __name__ == '__main__':
    main()

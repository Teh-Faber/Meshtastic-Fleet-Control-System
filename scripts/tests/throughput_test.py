#!/usr/bin/env python3
"""
Measure delivered data rate to the echo device.

Each run publishes ``count`` messages of a given size on
``/link_test/data`` (std_msgs/UInt8MultiArray) at a fixed interval, then
asks the echo device how many arrived (``/link_test/report_request``,
repeated until ``/<peer>/link_test/report`` comes back).

One process can do a whole sweep: ``payload_sizes`` and ``intervals`` (comma
separated, e.g. ``16,64,200``) run every combination one after the other
over the same bridge connection, so the bridge start and the handshake
happen once. Without them it is a single run of ``payload_size`` every
``interval_s``.

The bridge keeps only the latest message per topic, so messages published
faster than the radio can send are overwritten and count as lost. Finding
that point is the goal of this test. Mind the EU868 duty-cycle limits.

Logs progress while sending and one result line per run. Appends one row
per run to ``throughput_summary.csv``, with both GPS positions and their
distance when the T-Beams have a fix (the echo device's position comes with
its report).
"""

import json
import time

from link_test_common import (append_summary, DATA, DATA_HEADER, format_duration, HostTest,
                              parse_list, REPORT, REPORT_REQUEST, run, TopicWatcher)
from std_msgs.msg import String, UInt8MultiArray

# Wait this long after the last message before the first report request.
REPORT_DELAY_S = 1.0
REPORT_RETRY_S = 2.0
# Log sending progress at most this often.
PROGRESS_PERIOD_S = 5.0


class ThroughputTest(HostTest):

    def __init__(self):
        super().__init__('throughput_test')
        self.count = self.param('count', 20)
        sizes = parse_list(self.param('payload_sizes', ''), int) or \
            [self.param('payload_size', 200)]
        intervals = parse_list(self.param('intervals', ''), float) or \
            [self.param('interval_s', 1.0)]
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
        self.report = None
        self.t_start = None
        self.t_last_progress = 0.0
        self.t_next_request = 0.0
        self.summary_path = None
        self.results = []      # (size, interval, received, sent)

    # --- run sequencing --------------------------------------------------

    @property
    def run_id_now(self):
        # Every run gets its own id; the echo device keeps counts per id.
        return (self.run_id + 1 + self.index) & 0xFFFF

    def start(self):
        self.t_start = time.monotonic()
        send_s = sum(self.count * interval for _, interval in self.runs)
        overhead_s = len(self.runs) * (REPORT_DELAY_S + 1.0 + self.pause_s)
        self.get_logger().info(
            f'Throughput: {len(self.runs)} run(s) of {self.count} messages, '
            f'about {format_duration(send_s + overhead_s)}')
        self.create_timer(0.1, self.control)
        self.next_run()

    def next_run(self):
        self.index += 1
        if self.index >= len(self.runs):
            self.done = True
            return
        size, interval = self.runs[self.index]
        self.seq = 0
        self.report = None
        self.set_state('send')
        self.t_last_progress = time.monotonic()
        self.get_logger().info(
            f'{self.run_label()}: {self.count} x {size} B every {interval}s '
            f'(offered {size / interval:.1f} B/s, ~{format_duration(self.count * interval)})')
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
            f'{self.run_label()}: {delivered}'
            + ''.join(f', {d}' for d in details if d))
        self.set_state('pause')
        if self.index + 1 >= len(self.runs):
            self.next_run()  # last run: no pause

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
            'payload_size': size,
            'interval_s': interval,
            'offered_Bps': round(size / interval, 1),
            'sent': self.seq,
            'received': received,
            'pdr': round(received / self.seq, 3) if received is not None and self.seq else None,
            'goodput_Bps': round(goodput, 1) if goodput else None,
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
        log.info(f'Throughput done: {len(self.results)}/{len(self.runs)} run(s) in '
                 f'{format_duration(time.monotonic() - self.t_start)}')
        if len(self.results) > 1:
            log.info('  size  interval  delivered')
            for row in self.results:
                pdr = f'{row["pdr"]:.0%}' if row['pdr'] is not None else 'no report'
                log.info(f'  {row["payload_size"]:>4} B  {row["interval_s"]:>6}s  '
                         f'{row["received"]}/{row["sent"]} ({pdr})')
        if self.summary_path:
            log.info(f'Summary in {self.summary_path}')


def main(args=None):
    run(ThroughputTest, args)


if __name__ == '__main__':
    main()

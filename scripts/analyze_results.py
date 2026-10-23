#!/usr/bin/env python3
"""
Summarize the link test results.

Usage: python3 scripts/analyze_results.py [results dir, default ~/fleet_test_results]

Prints latency_summary.csv and throughput_summary.csv as tables (one row
per run, grouped by label), followed by per-ping RTT and link statistics for
each latency_*.csv run log. Uses only the standard library.

TODO: plot RTT and goodput against label/distance for the report.
"""

import argparse
import csv
import glob
import math
import os
import statistics


def read_rows(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def floats(rows, column):
    out = []
    for row in rows:
        try:
            out.append(float(row[column]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def print_table(rows, columns):
    widths = {c: max([len(c)] + [len(str(r.get(c) or '')) for r in rows]) for c in columns}
    print('  ' + '  '.join(c.ljust(widths[c]) for c in columns))
    for row in sorted(rows, key=lambda r: (r.get('label') or '', r.get('time') or '')):
        print('  ' + '  '.join(str(row.get(c) or '').ljust(widths[c]) for c in columns))


def summarize_run(rows):
    rtts = sorted(floats(rows, 'rtt_ms'))
    ok = sum(1 for row in rows if row.get('status') == 'ok')
    line = f'answered {ok}/{len(rows)}'
    if rtts:
        p95 = rtts[max(0, math.ceil(0.95 * len(rtts)) - 1)]
        line += (f', RTT mean {statistics.fmean(rtts):.0f} median '
                 f'{statistics.median(rtts):.0f} p95 {p95:.0f} ms')
    rssi, snr = floats(rows, 'rssi'), floats(rows, 'snr')
    if rssi:
        line += f', RSSI {statistics.fmean(rssi):.1f}'
    if snr:
        line += f', SNR {statistics.fmean(snr):.1f}'
    return line


def main():
    parser = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    parser.add_argument('results_dir', nargs='?',
                        default=os.path.expanduser('~/fleet_test_results'))
    args = parser.parse_args()

    tables = {
        'latency_summary.csv': ['label', 'time', 'payload_size', 'sent', 'answered', 'pdr',
                                'rtt_median_ms', 'rtt_p95_ms', 'rssi_mean', 'snr_mean', 'hops'],
        'throughput_summary.csv': ['label', 'time', 'payload_size', 'interval_s', 'offered_Bps',
                                   'sent', 'received', 'pdr', 'goodput_Bps', 'rssi_mean',
                                   'snr_mean', 'hops'],
    }
    for name, columns in tables.items():
        path = os.path.join(args.results_dir, name)
        if os.path.exists(path):
            print(name)
            print_table(read_rows(path), columns)
            print()

    for path in sorted(glob.glob(os.path.join(args.results_dir, 'latency_*.csv'))):
        if not path.endswith('_summary.csv'):
            print(f'{os.path.basename(path)}: {summarize_run(read_rows(path))}')


if __name__ == '__main__':
    main()

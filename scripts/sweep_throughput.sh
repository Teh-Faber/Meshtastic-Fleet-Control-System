#!/usr/bin/env bash
# Run the throughput test over a grid of message sizes and publish intervals.
#
# Usage: scripts/sweep_throughput.sh [count per run] [launch args...]
#   e.g. scripts/sweep_throughput.sh 30 label:=500m
#   The remote device just keeps running `scripts/run_test.sh echo`.
#
# Every run appends a row to ~/fleet_test_results/throughput_summary.csv.
# Duty cycle: Meshtastic's EU_868 region allows 10 % airtime (360 s per
# rolling hour) and the firmware stops transmitting once that is used up.
# On LONG_FAST a 200 B message is ~2 s on air, so the full grid below
# (16 runs x count messages) far exceeds one hour's budget. Either lower
# the count, sweep fewer combinations per hour, use a faster modem preset,
# or raise PAUSE_S so the budget recovers between runs. Runs measured
# after the budget is exhausted show the limiter, not the radio link.
set -euo pipefail

count="${1:-50}"
shift || true

PAYLOAD_SIZES=(16 64 128 200)
INTERVALS=(10.0 5.0 2.0 1.0)
# Seconds between runs; override with PAUSE_S=600 scripts/sweep_throughput.sh ...
PAUSE_S="${PAUSE_S:-30}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for size in "${PAYLOAD_SIZES[@]}"; do
    for interval in "${INTERVALS[@]}"; do
        echo "=== payload ${size} B, interval ${interval} s ==="
        "$script_dir/run_test.sh" throughput count:="$count" \
            payload_size:="$size" interval_s:="$interval" "$@"
        # Let the firmware TX queue drain (and some duty-cycle budget recover).
        sleep "$PAUSE_S"
    done
done

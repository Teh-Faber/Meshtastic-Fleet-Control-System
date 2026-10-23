#!/usr/bin/env bash
# Run the throughput test over a grid of message sizes and publish intervals.
#
# Usage: scripts/sweep_throughput.sh [count per run] [launch args...]
#   e.g. scripts/sweep_throughput.sh 30 label:=500m
#   The remote device just keeps running `scripts/run_test.sh echo`.
#
# Every run appends a row to ~/fleet_test_results/throughput_summary.csv.
# A full sweep takes a long time at LoRa data rates, and the short intervals
# deliberately overload the channel. Check the EU868 duty-cycle limit first.
set -euo pipefail

count="${1:-50}"
shift || true

PAYLOAD_SIZES=(16 64 128 200)
INTERVALS=(10.0 5.0 2.0 1.0)

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for size in "${PAYLOAD_SIZES[@]}"; do
    for interval in "${INTERVALS[@]}"; do
        echo "=== payload ${size} B, interval ${interval} s ==="
        "$script_dir/run_test.sh" throughput count:="$count" \
            payload_size:="$size" interval_s:="$interval" "$@"
        # Let the firmware TX queue drain before the next run.
        sleep 30
    done
done

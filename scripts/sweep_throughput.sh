#!/usr/bin/env bash
# Run the throughput test over a grid of message sizes and publish intervals.
#
# Usage: scripts/sweep_throughput.sh [count per run] [launch args...]
#   e.g. scripts/sweep_throughput.sh 20 label:=500m
#   The remote device just keeps running `scripts/run_test.sh echo`.
#   Override the grid: SIZES=16,200 INTERVALS=2,1,0.5 scripts/sweep_throughput.sh
#
# The whole grid runs in one throughput_test process over one bridge
# connection, so the bridge start and the handshake happen once. With the
# defaults below that is 12 runs of 20 messages, about 3 min in all: ~2 min
# of sending plus ~4 s per run for the report and the pause. The intervals
# bracket where SHORT_FAST saturates; on LONG_FAST use slower ones
# (e.g. INTERVALS=4,2,1).
#
# Every run appends a row to ~/fleet_test_results/throughput_summary.csv.
# Duty cycle: Meshtastic's EU_868 region allows 10 % airtime (360 s per
# rolling hour) and the firmware stops transmitting once that is used up.
# On SHORT_FAST a 200 B message is ~0.2 s on air and the default grid uses
# well under a minute of airtime. On LONG_FAST it is ~2 s per 200 B message
# and the fast intervals saturate the radio, so a full grid can use most of
# the hour's budget: sweep fewer sizes per hour there. Runs measured after
# the budget is exhausted show the limiter, not the radio link.
set -euo pipefail

count="${1:-20}"
shift || true

SIZES="${SIZES:-16,64,200}"
INTERVALS="${INTERVALS:-1.0,0.5,0.25,0.1}"
# Seconds between runs, lets the radio's transmit queue drain.
PAUSE_S="${PAUSE_S:-2.0}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Sweep: sizes ${SIZES} B x intervals ${INTERVALS} s, ${count} messages per run"
exec "$script_dir/run_test.sh" throughput count:="$count" \
    payload_sizes:="$SIZES" intervals:="$INTERVALS" pause_s:="$PAUSE_S" "$@"

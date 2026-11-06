#!/usr/bin/env bash
# Run the throughput test over a grid of message sizes and publish intervals.
#
# Usage: scripts/sweep_throughput.sh [seconds per run] [launch args...]
#   e.g. scripts/sweep_throughput.sh 10 label:=500m
#        scripts/sweep_throughput.sh label:=500m        (10 s per run)
#   The remote device just keeps running `scripts/run_test.sh echo`.
#   Override the grid: SIZES=16,210 INTERVALS=2,1,0.5 scripts/sweep_throughput.sh
#
# To just find the maximum, `scripts/run_test.sh throughput` is quicker: it
# ramps the rate up for one size and stops a couple of steps past the limit.
# The sweep measures every combination, e.g. to plot goodput against size.
#
# The whole grid runs in one throughput_test process over one bridge
# connection, so the bridge start and the handshake happen once. Each run
# lasts the given seconds, so fast intervals send enough messages to
# overflow the radio's transmit queue (~16 packets) and show the real limit.
# With the defaults below: 12 runs of 10 s, about 3 min in all. The
# intervals go from well below to well past where SHORT_FAST saturates; on
# LONG_FAST use slower ones (e.g. INTERVALS=8,4,2,1).
#
# Every run appends a row to ~/fleet_test_results/throughput_summary.csv.
# Duty cycle: Meshtastic's EU_868 region allows 10 % airtime (360 s per
# rolling hour) and the firmware stops transmitting once that is used up.
# Runs past the limit keep the radio transmitting for their whole duration:
# on SHORT_FAST the default grid uses roughly 1-2 min of airtime, so about
# two sweeps per hour. Runs measured after the budget is exhausted show the
# limiter, not the radio link.
set -euo pipefail

# The first argument is the run length only if it is a number, so
# `sweep_throughput.sh label:=100m` works too.
duration=10
if [ $# -ge 1 ] && [[ "$1" =~ ^[0-9]+([.][0-9]+)?$ ]]; then
    duration="$1"
    shift
elif [ $# -ge 1 ] && [[ "$1" != *:=* ]]; then
    echo "Expected seconds per run or launch arguments (name:=value), got '$1'" >&2
    exit 1
fi

SIZES="${SIZES:-16,64,210}"
INTERVALS="${INTERVALS:-1.0,0.5,0.2,0.1}"
# Seconds between runs, lets the radio's transmit queue drain.
PAUSE_S="${PAUSE_S:-2.0}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Sweep: sizes ${SIZES} B x intervals ${INTERVALS} s, ${duration} s per run"
exec "$script_dir/run_test.sh" throughput duration_s:="$duration" \
    payload_sizes:="$SIZES" intervals:="$INTERVALS" pause_s:="$PAUSE_S" "$@"

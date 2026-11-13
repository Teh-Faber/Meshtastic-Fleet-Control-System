#!/usr/bin/env bash
# Ramp the throughput test for several message sizes, every step of each.
#
# Usage: scripts/sweep_throughput.sh [seconds per run] [launch args...]
#   e.g. scripts/sweep_throughput.sh label:=500m          (10 s per run)
#        scripts/sweep_throughput.sh 15 label:=500m
#        MAX_RATE=400 STEPS=8 SIZES=64,200 scripts/sweep_throughput.sh
#        SIZES=200,400,800 scripts/sweep_throughput.sh   (400 and 800 B: 2 and 4 packets)
#        SIZES=1600 scripts/sweep_throughput.sh max_fragments:=8
#   The remote device just keeps running `scripts/run_test.sh echo`.
#
# Each size gets the same ramp as `scripts/run_test.sh throughput`: STEPS
# evenly spaced data rates up to MAX_RATE B/s, each capped at
# MAX_PACKET_RATE radio packets/s so neither small nor fragmented messages
# flood the radio. Sizes the bridge can't send (more than max_fragments
# packets, 848 B by default) are skipped with an error. Unlike the plain
# throughput test it runs every step instead of stopping once saturated,
# so every size gets a complete curve. With the defaults below: 3 sizes x 6
# steps of 10 s, about 4 min, one bridge start and one handshake.
#
# To just find the maximum, `scripts/run_test.sh throughput` is quicker.
# For a fixed grid of intervals instead, run the throughput test with
# payload_sizes:=... intervals:=... directly.
#
# Every run appends a row to <workspace>/results/throughput_summary.csv.
# Duty cycle: Meshtastic's EU_868 region allows 10 % airtime (360 s per
# rolling hour) and the firmware stops transmitting once that is used up
# (the bridge then logs DUTY_CYCLE_LIMIT). Saturated steps keep the radio
# transmitting for their whole duration, so leave time between sweeps.
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

SIZES="${SIZES:-16,64,200}"
MAX_RATE="${MAX_RATE:-800}"
STEPS="${STEPS:-6}"
MAX_PACKET_RATE="${MAX_PACKET_RATE:-4}"
# Seconds between runs, lets the radio's transmit queue drain.
PAUSE_S="${PAUSE_S:-2.0}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Sweep: sizes ${SIZES} B, ${STEPS} steps up to ${MAX_RATE} B/s (at most" \
     "${MAX_PACKET_RATE} packets/s), ${duration} s per run"
exec "$script_dir/run_test.sh" throughput duration_s:="$duration" \
    payload_sizes:="$SIZES" max_rate_Bps:="$MAX_RATE" ramp_steps:="$STEPS" \
    max_packet_rate:="$MAX_PACKET_RATE" overload_steps:=0 pause_s:="$PAUSE_S" "$@"

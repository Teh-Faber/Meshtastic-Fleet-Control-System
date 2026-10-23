#!/usr/bin/env bash
# Start the bridge plus one test node through its launch file.
#
# Usage: scripts/run_test.sh <echo|latency|throughput|device> [launch args...]
#   e.g. scripts/run_test.sh echo                        (once, on the remote device)
#        scripts/run_test.sh latency label:=500m          (on the host, repeat as needed)
#
# Sources install/setup.bash from WORKSPACE (default: current directory) if present.
set -euo pipefail

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
}

[ $# -ge 1 ] || usage
test_name="$1"
shift

case "$test_name" in
    latency|throughput) launch_file="${test_name}_test.launch.py" ;;
    echo) launch_file="echo_device.launch.py" ;;
    device) launch_file="emulated_device.launch.py" ;;
    *) usage ;;
esac

workspace="${WORKSPACE:-$(pwd)}"
if [ -f "$workspace/install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source "$workspace/install/setup.bash"
fi

exec ros2 launch meshtastic_bridge "$launch_file" "$@"

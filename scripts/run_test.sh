#!/usr/bin/env bash
# Start the bridge plus one test node through its launch file.
#
# Usage: scripts/run_test.sh <echo|latency|throughput|device> [launch args...]
#   e.g. scripts/run_test.sh echo                        (once, on the remote device)
#        scripts/run_test.sh latency label:=500m          (on the host, repeat as needed)
#
# Sources install/setup.bash from WORKSPACE, else the current directory, else the
# workspace this script is in. Works from any directory.
set -euo pipefail

usage() {
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
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

# Workspace: $WORKSPACE, else the current directory, else the one this script
# lives in (<workspace>/src/meshtastic_bridge/scripts).
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
workspace="${WORKSPACE:-}"
if [ -z "$workspace" ]; then
    for candidate in "$(pwd)" "$script_dir/../../.."; do
        if [ -f "$candidate/install/setup.bash" ]; then
            workspace="$candidate"
            break
        fi
    done
fi
if [ -n "$workspace" ] && [ -f "$workspace/install/setup.bash" ]; then
    # colcon's setup scripts read unset variables (COLCON_TRACE, ...), which
    # `set -u` turns into a fatal error.
    set +u
    # shellcheck disable=SC1091
    source "$workspace/install/setup.bash"
    set -u
fi

exec ros2 launch meshtastic_bridge "$launch_file" "$@"

"""
Shared helpers for the link tests.

The remote device runs echo_device.py permanently. The host runs one test
at a time (latency_test.py, throughput_test.py) against it. Both sides only
publish and subscribe to ordinary ROS topics under ``/link_test`` and let
their bridges carry them, so the tests cover the whole path. Topics from the
other device appear locally as ``/<device>/link_test/...``, which
``TopicWatcher`` discovers at runtime.

GPS: each bridge publishes its radio's own fix on ``gps_topic`` (T-Beam
boards). The echo device forwards its fix on ``/link_test/position``, so
host results carry both positions and the distance between the two ends.
Boards without GPS just leave those columns empty.
"""

import collections
import csv
import math
import os
import secrets
import statistics
import struct
import time

from diagnostic_msgs.msg import DiagnosticStatus
from rcl_interfaces.msg import ParameterDescriptor
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import NavSatFix, NavSatStatus
from std_msgs.msg import Header

ROOT = '/link_test'
PING = f'{ROOT}/ping'                      # host -> echo, std_msgs/Header
PONG = f'{ROOT}/pong'                      # echo -> host, std_msgs/Header
DATA = f'{ROOT}/data'                      # host -> echo, std_msgs/UInt8MultiArray
REPORT_REQUEST = f'{ROOT}/report_request'  # host -> echo, std_msgs/String (run id)
REPORT = f'{ROOT}/report'                  # echo -> host, std_msgs/String (JSON)
POSITION = f'{ROOT}/position'              # echo -> host, sensor_msgs/NavSatFix

# Where the local bridge publishes the radio's GPS fix (bridge gps_topic).
GPS_TOPIC = '/mesh/gps/fix'
EARTH_RADIUS_M = 6371008.8

# Start of every DATA payload: run id, seq, number of messages in the run.
DATA_HEADER = struct.Struct('<HII')


class LinkTestNode(Node):
    """
    Base class for the test nodes.

    Subclasses set ``self.done = True`` when finished and override
    ``finish()`` to write their results.
    """

    def __init__(self, name):
        super().__init__(name)
        self.done = False
        self.output_dir = os.path.expanduser(self.param('output_dir', '~/fleet_test_results'))
        # Fixes older than this are treated as no fix.
        self.max_fix_age_s = self.param('max_fix_age_s', 300.0)
        self.gps = GpsFix(self, self.param('gps_topic', GPS_TOPIC))

    def param(self, name, default):
        """Declare ``name`` and return its value cast to the type of ``default``."""
        # dynamic_typing lets `-p interval_s:=5` through as an int; cast it back here.
        self.declare_parameter(name, default, ParameterDescriptor(dynamic_typing=True))
        return type(default)(self.get_parameter(name).value)

    def finish(self):
        """Write the results. Called once on shutdown."""


class HostTest(LinkTestNode):
    """
    Base class for tests run on the host against an always-on echo_device.

    Starts with a handshake: unnumbered pings every 2 s until the echo device
    answers. That also gives both bridges time to discover and announce the
    test topics, so the measured part of the test starts on a warm link.
    Then ``start()`` is called.
    """

    def __init__(self, name):
        super().__init__(name)
        # Free text stored with the results, e.g. "500m" or "relay_1hop".
        self.label = self.param('label', '')
        self.handshake_timeout_s = self.param('handshake_timeout_s', 120.0)

        self.run_id = secrets.randbits(16)
        self.peer = None
        self.link = LinkQuality(self, self.param('link_quality_topic', '/mesh/link_quality'))
        self.ping_pub = self.create_publisher(Header, PING, 10)
        self.pong_watcher = TopicWatcher(self, PONG, Header, self._on_pong)
        self.peer_fixes = {}  # device -> latest NavSatFix it forwarded
        self.position_watcher = TopicWatcher(
            self, POSITION, NavSatFix, lambda d, m: self.peer_fixes.__setitem__(d, m))

        self._t_handshake = time.monotonic()
        self._handshake_timer = self.create_timer(2.0, self._handshake_tick)
        self.get_logger().info('Waiting for the echo device...')

    def publish_ping(self, tag, pad_to=0):
        msg = Header(frame_id=f'{self.run_id}:{tag}:'.ljust(pad_to, '.'))
        msg.stamp = self.get_clock().now().to_msg()
        self.ping_pub.publish(msg)

    def _handshake_tick(self):
        if time.monotonic() - self._t_handshake > self.handshake_timeout_s:
            self.get_logger().error(
                f'No answer from an echo device within {self.handshake_timeout_s:.0f}s')
            self._handshake_timer.cancel()
            self.done = True
            return
        self.publish_ping('h')

    def _on_pong(self, device, msg):
        try:
            run_id, tag, _ = msg.frame_id.split(':', 2)
            if int(run_id) != self.run_id:
                return
        except ValueError:
            return
        if tag == 'h':
            if self.peer is None:
                self.peer = device
                self._handshake_timer.cancel()
                self.get_logger().info(f'Echo device "{device}" answered, starting test')
                self.start()
            return
        try:
            self.on_pong(device, int(tag))
        except ValueError:
            pass

    def start(self):
        """Begin the measured part of the test."""

    def on_pong(self, device, seq):
        """Handle the pong for numbered ping ``seq``."""

    def peer_position(self):
        """Return the peer's latest forwarded fix as a dict, or None."""
        return fix_to_dict(self.peer_fixes.get(self.peer), self.max_fix_age_s)

    def position_fields(self, peer_pos=None):
        """
        Host and peer position plus the distance between them, for a result row.

        ``peer_pos`` defaults to the fix the echo device last forwarded.
        """
        host = self.gps.position(self.max_fix_age_s)
        peer = peer_pos if peer_pos is not None else self.peer_position()
        return {
            'host_lat': host and round(host['lat'], 7),
            'host_lon': host and round(host['lon'], 7),
            'peer_lat': peer and round(peer['lat'], 7),
            'peer_lon': peer and round(peer['lon'], 7),
            'distance_m': round(distance_m(host, peer), 1) if host and peer else None,
        }

    def link_summary(self, source_topic):
        """Return mean RSSI/SNR and last hop count seen from the peer on ``source_topic``."""
        samples = self.link.history.get((self.peer, source_topic), [])
        return summarize_link(samples)


class LinkQuality:
    """Keep the bridge's link quality reports per (device, source topic)."""

    def __init__(self, node, topic, history=1000):
        self.latest = {}
        self.history = {}
        self._maxlen = history  # bounded, the echo device runs indefinitely
        node.create_subscription(DiagnosticStatus, topic, self._on_status, 10)

    def _on_status(self, msg):
        values = {kv.key: kv.value for kv in msg.values}
        key = (msg.name, values.get('source_topic'))
        self.latest[key] = values
        self.history.setdefault(key, collections.deque(maxlen=self._maxlen)).append(values)

    def get(self, device, source_topic):
        return self.latest.get((device, source_topic), {})


class GpsFix:
    """Latest GPS fix of the local radio, as published by the bridge."""

    def __init__(self, node, topic):
        self.latest = None
        # Best effort matches the bridge's reliable publisher too.
        node.create_subscription(NavSatFix, topic, self._on_fix, 10)

    def _on_fix(self, msg):
        if msg.status.status >= NavSatStatus.STATUS_FIX:
            self.latest = msg

    def position(self, max_age_s):
        return fix_to_dict(self.latest, max_age_s)


def fix_to_dict(fix, max_age_s):
    """
    ``{'lat', 'lon', 'alt', 'age_s'}`` of a NavSatFix, or None if missing or stale.

    The age compares the GPS fix time with this machine's clock, so it is
    only as good as the local clock (NTP keeps it well within a second).
    """
    if fix is None or fix.status.status < NavSatStatus.STATUS_FIX:
        return None
    age = time.time() - (fix.header.stamp.sec + fix.header.stamp.nanosec * 1e-9)
    if age > max_age_s:
        return None
    return {'lat': fix.latitude, 'lon': fix.longitude,
            'alt': None if math.isnan(fix.altitude) else fix.altitude, 'age_s': max(0.0, age)}


def distance_m(a, b):
    """Great-circle distance in metres between two ``{'lat', 'lon'}`` dicts."""
    lat1, lat2 = math.radians(a['lat']), math.radians(b['lat'])
    dlat = lat2 - lat1
    dlon = math.radians(b['lon'] - a['lon'])
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(min(1.0, h)))


class TopicWatcher:
    """
    Subscribe to every remote copy of a topic as the bridge creates it.

    Matches topics named ``/<device><suffix>``. Devices appear at runtime, so
    the graph is rescanned every ``period_s``. ``callback(device, msg)`` gets
    the device name taken from the topic.
    """

    def __init__(self, node, suffix, msg_type, callback, period_s=1.0):
        self.node = node
        self.suffix = suffix
        self.msg_type = msg_type
        self.callback = callback
        self.subs = {}
        node.create_timer(period_s, self.scan)

    def scan(self):
        for name, _ in self.node.get_topic_names_and_types():
            if name == self.suffix or not name.endswith(self.suffix) or name in self.subs:
                continue
            device = name[:-len(self.suffix)].rsplit('/', 1)[-1]
            self.subs[name] = self.node.create_subscription(
                self.msg_type, name, lambda msg, d=device: self.callback(d, msg), 10)
            self.node.get_logger().info(f'Watching {name}')


class CsvLog:
    """Write rows to a new timestamped CSV file in ``output_dir``."""

    def __init__(self, output_dir, test_name, fieldnames):
        os.makedirs(output_dir, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        self.path = os.path.join(output_dir, f'{test_name}_{stamp}.csv')
        self._file = open(self.path, 'w', newline='')
        self._writer = csv.DictWriter(self._file, fieldnames=fieldnames, extrasaction='ignore')
        self._writer.writeheader()

    def write(self, **row):
        self._writer.writerow(row)
        # Flush every row so a crash or Ctrl+C mid-test keeps the data.
        self._file.flush()

    def close(self):
        self._file.close()


def append_summary(output_dir, test_name, row):
    """
    Append one result row to ``<test_name>_summary.csv``, one row per run.

    If the file was written with other columns (an older version of the
    tests), it is rewritten with the union of both, old rows left blank.
    """
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f'{test_name}_summary.csv')
    if os.path.exists(path):
        with open(path, newline='') as f:
            reader = csv.DictReader(f)
            old_columns = reader.fieldnames or []
            old_rows = list(reader) if set(row) - set(old_columns) else None
        if old_rows is not None:
            columns = old_columns + [c for c in row if c not in old_columns]
            with open(path, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=columns)
                writer.writeheader()
                writer.writerows(old_rows)
        else:
            columns = old_columns
    else:
        columns = list(row)
        with open(path, 'w', newline='') as f:
            csv.DictWriter(f, fieldnames=columns).writeheader()
    with open(path, 'a', newline='') as f:
        csv.DictWriter(f, fieldnames=columns, extrasaction='ignore').writerow(row)
    return path


def _floats(samples, key):
    out = []
    for sample in samples:
        try:
            out.append(float(sample[key]))
        except (KeyError, TypeError, ValueError):
            pass
    return out


def summarize_link(samples):
    """Mean RSSI/SNR and the most recent hop count of link quality samples."""
    rssi, snr = _floats(samples, 'rssi'), _floats(samples, 'snr')
    return {
        'rssi_mean': round(statistics.fmean(rssi), 1) if rssi else None,
        'snr_mean': round(statistics.fmean(snr), 2) if snr else None,
        'hops': samples[-1].get('hops') if samples else None,
    }


def summarize(values):
    """Return count/mean/median/p95/min/max of ``values``."""
    if not values:
        return {'count': 0}
    ordered = sorted(values)
    return {
        'count': len(ordered),
        'mean': statistics.fmean(ordered),
        'median': statistics.median(ordered),
        'p95': ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
        'min': ordered[0],
        'max': ordered[-1],
    }


def format_summary(label, stats, unit=''):
    """Format ``summarize()`` output as one log line."""
    if stats['count'] == 0:
        return f'{label}: no samples'
    return (f'{label}: n={stats["count"]} mean={stats["mean"]:.1f}{unit} '
            f'median={stats["median"]:.1f}{unit} p95={stats["p95"]:.1f}{unit} '
            f'min={stats["min"]:.1f}{unit} max={stats["max"]:.1f}{unit}')


def run(node_cls, args=None):
    """Spin ``node_cls`` until it sets ``done`` or is interrupted, then finish."""
    rclpy.init(args=args)
    node = node_cls()
    try:
        while rclpy.ok() and not node.done:
            rclpy.spin_once(node, timeout_sec=0.1)
    except KeyboardInterrupt:
        pass
    finally:
        node.finish()
        node.destroy_node()
        rclpy.try_shutdown()

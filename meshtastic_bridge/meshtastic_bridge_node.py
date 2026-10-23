"""
Bridge local ROS 2 topics to a Meshtastic mesh and back.

TX: scans the ROS graph every ``scan_period_s``, subscribes to each topic
that matches ``include_topics`` and none of ``exclude_topics``, and sends
the latest message of each topic into the mesh, rate limited per topic.

RX: messages from other devices are published locally with their original
type under ``/<remote_namespace>/<device_name>/<topic>``, e.g.
``/drone1/gps/fix``. Link quality for every received message is published
on ``link_quality_topic``.

Every device runs the same node. ``rx_enabled: false`` makes a device
transmit only (e.g. an autonomous drone that never needs fleet data), and
``tx_enabled: false`` makes it listen only. Received traffic can be limited
to selected devices (``allow_devices``/``block_devices``) and topics
(``rx_include_topics``/``rx_exclude_topics``).

The radio must run Meshtastic firmware; the bridge talks to it through
Meshtastic's serial (or TCP) API. On connect it logs hardware model,
firmware version and LoRa region, and warns about settings that stop
the link from working.

TODO: reconnect after the serial link drops, unsubscribe from topics
that disappear, optional ACKs for selected topics.
"""

import fnmatch
import queue
import re
import socket
import time

from diagnostic_msgs.msg import DiagnosticStatus, KeyValue
from meshtastic.protobuf import config_pb2, portnums_pb2
import meshtastic.serial_interface
import meshtastic.tcp_interface
import meshtastic.util
from meshtastic_bridge import codec
from pubsub import pub
import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.qos import QoSProfile, ReliabilityPolicy
from rclpy.serialization import deserialize_message
from rosidl_runtime_py.utilities import get_message


def sanitize_name(name):
    """Turn ``name`` into a valid ROS name token."""
    name = re.sub(r'[^A-Za-z0-9_]', '_', name).strip('_')
    if not name or name[0].isdigit():
        name = 'node_' + name
    return name


def default_device_name(node_id):
    """Device name for a Meshtastic node id such as ``!a1b2c3d4``."""
    return 'node_' + node_id.lstrip('!')


def matches(name, patterns):
    return any(fnmatch.fnmatchcase(name, pattern) for pattern in patterns)


def version_tuple(version):
    """Turn a firmware version like ``2.5.6.d0f8a43`` into ``(2, 5, 6)``."""
    parts = []
    for part in version.split('.')[:3]:
        digits = re.match(r'\d+', part)
        parts.append(int(digits.group()) if digits else 0)
    return tuple(parts)


class TxTopic:
    """A local topic being sent into the mesh."""

    def __init__(self, name, msg_type, min_interval_s):
        self.name = name
        self.msg_type = msg_type
        self.topic_id = codec.topic_id(name, msg_type)
        self.min_interval_s = min_interval_s
        self.subscription = None
        self.latest = None  # serialized message waiting to be sent
        self.last_sent = 0.0
        self.seq = 0


class RemoteTopic:
    """A topic from another device being republished locally."""

    def __init__(self, device, source_topic, local_name, msg_cls, publisher):
        self.device = device
        self.source_topic = source_topic
        self.local_name = local_name
        self.msg_cls = msg_cls
        self.publisher = publisher  # None if the message type is not installed


class BridgeNode(Node):
    """Bridge filtered local topics into the mesh and remote topics back out."""

    def __init__(self):
        super().__init__('meshtastic_bridge_node')

        self.declare_parameter('serial_port', 'auto')
        self.declare_parameter('tcp_host', '')
        self.declare_parameter('connect_timeout_s', 30)
        self.declare_parameter('expected_hw_model', 'TLORA_T3_S3')
        self.declare_parameter('min_firmware_version', '')
        self.declare_parameter('device_name', '')
        self.declare_parameter('tx_enabled', True)
        self.declare_parameter('rx_enabled', True)
        self.declare_parameter('include_topics', ['*'])
        self.declare_parameter('exclude_topics', ['/rosout', '/parameter_events', '/mesh/*'])
        self.declare_parameter('allow_devices', ['*'])
        self.declare_parameter('block_devices', Parameter.Type.STRING_ARRAY)
        self.declare_parameter('rx_include_topics', ['*'])
        self.declare_parameter('rx_exclude_topics', Parameter.Type.STRING_ARRAY)
        self.declare_parameter('topic_intervals', Parameter.Type.STRING_ARRAY)
        self.declare_parameter('min_interval_s', 10.0)
        self.declare_parameter('max_fragments', 4)
        self.declare_parameter('scan_period_s', 5.0)
        self.declare_parameter('announce_period_s', 300.0)
        self.declare_parameter('remote_namespace', '')
        self.declare_parameter('link_quality_topic', '/mesh/link_quality')
        self.declare_parameter('dest', '^all')
        self.declare_parameter('channel_index', 0)
        self.declare_parameter('hop_limit', 0)
        self.declare_parameter('port_num', 'PRIVATE_APP')
        self.declare_parameter('max_payload_bytes', 233)
        self.declare_parameter('poll_hz', 20.0)

        self.serial_port = self.param('serial_port')
        self.tcp_host = self.param('tcp_host')
        self.connect_timeout_s = self.param('connect_timeout_s')
        self.expected_hw_model = self.param('expected_hw_model')
        self.min_firmware_version = self.param('min_firmware_version')
        self.tx_enabled = self.param('tx_enabled')
        self.rx_enabled = self.param('rx_enabled')
        self.include_topics = self.param('include_topics')
        self.exclude_topics = self.param('exclude_topics')
        self.allow_devices = self.param('allow_devices') or ['*']
        self.block_devices = self.param('block_devices') or []
        self.rx_include_topics = self.param('rx_include_topics') or ['*']
        self.rx_exclude_topics = self.param('rx_exclude_topics') or []
        self.interval_rules = self.parse_intervals(self.param('topic_intervals') or [])
        self.min_interval_s = self.param('min_interval_s')
        self.max_fragments = self.param('max_fragments')
        self.remote_namespace = self.param('remote_namespace').strip('/')
        self.dest = self.param('dest')
        self.channel_index = self.param('channel_index')
        self.hop_limit = self.param('hop_limit')
        self.port_name = self.param('port_num')
        self.port_num = portnums_pb2.PortNum.Value(self.port_name)
        self.max_payload_bytes = self.param('max_payload_bytes')

        self.tx_topics = {}       # local topic name -> TxTopic
        self.remote_topics = {}   # (sender id, topic id) -> RemoteTopic
        self.device_names = {}    # sender id -> device name
        self.own_topics = set()   # topics this node publishes; never sent back out
        self.unsupported = set()  # local topics whose type could not be loaded
        self.last_request = {}    # sender id -> time we last asked it to announce
        self.last_announce_all = 0.0
        self.reassembler = codec.Reassembler()

        # Best effort matches both reliable and best-effort publishers. Depth 1
        # since only the latest message per topic is ever sent.
        self.sub_qos = QoSProfile(depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)

        # Meshtastic callbacks run on the library's serial reader thread.
        # They only push onto this queue; poll() drains it on the executor.
        self._inbox = queue.Queue()
        self.interface = None
        pub.subscribe(self._on_receive, 'meshtastic.receive')
        pub.subscribe(self._on_connection_lost, 'meshtastic.connection.lost')
        self.connect()
        self.device_name = self.resolve_device_name(self.param('device_name'))

        if self.rx_enabled:
            self.link_pub = self.create_publisher(
                DiagnosticStatus, self.param('link_quality_topic'), 10)
            self.own_topics.add(self.link_pub.topic_name)
        self.create_timer(1.0 / self.param('poll_hz'), self.poll)
        if self.tx_enabled:
            self.create_timer(self.param('scan_period_s'), self.scan_topics)
            self.create_timer(self.param('announce_period_s'), self.announce_all)

        self.get_logger().info(
            f'Bridging as "{self.device_name}" (tx {"on" if self.tx_enabled else "off"}, '
            f'rx {"on" if self.rx_enabled else "off"})')

    def param(self, name):
        return self.get_parameter(name).value

    def parse_intervals(self, entries):
        """Parse ``topic_intervals`` entries of the form ``"<pattern>=<seconds>"``."""
        rules = []
        for entry in entries:
            pattern, sep, seconds = entry.rpartition('=')
            if sep:
                try:
                    rules.append((pattern.strip(), float(seconds)))
                    continue
                except ValueError:
                    pass
            self.get_logger().warn(f'Ignoring topic_intervals entry "{entry}"')
        return rules

    def interval_for(self, topic):
        for pattern, seconds in self.interval_rules:
            if fnmatch.fnmatchcase(topic, pattern):
                return seconds
        return self.min_interval_s

    # --- Meshtastic connection -------------------------------------------

    def find_serial_port(self):
        """Return the configured port, or the single Meshtastic-capable port found."""
        if self.serial_port not in ('', 'auto'):
            return self.serial_port
        # Our own detection instead of SerialInterface(devPath=None), which
        # calls sys.exit() when it finds zero or several candidate ports.
        ports = meshtastic.util.findPorts(True)
        if len(ports) == 1:
            self.get_logger().info(f'Auto-detected Meshtastic device on {ports[0]}')
            return ports[0]
        if not ports:
            self.get_logger().error('No Meshtastic device found. Is the board plugged in?')
        else:
            self.get_logger().error(
                f'Several possible devices found ({", ".join(ports)}); set serial_port')
        return None

    def open_interface(self):
        if self.tcp_host:
            return meshtastic.tcp_interface.TCPInterface(hostname=self.tcp_host)
        port = self.find_serial_port()
        if port is None:
            return None
        try:
            return meshtastic.serial_interface.SerialInterface(
                devPath=port, timeout=self.connect_timeout_s)
        except TypeError:
            # Older meshtastic releases have no timeout argument (fixed 300 s).
            return meshtastic.serial_interface.SerialInterface(devPath=port)

    def connect(self):
        """Connect to the Meshtastic device over TCP or serial and check its setup."""
        target = self.tcp_host or self.serial_port
        try:
            self.interface = self.open_interface()
        except Exception as e:
            # A board that opens but never answers is most likely not running
            # Meshtastic, e.g. still on LilyGo's factory test image.
            self.get_logger().error(
                f'Could not connect to a Meshtastic device on {target}: {e}. If the port opens '
                'but times out, the board is probably not running Meshtastic firmware; '
                'flash "Tlora T3s3 V1" from https://flasher.meshtastic.org')
            # TODO: retry on a timer instead of staying disconnected.
            self.interface = None
            return
        if self.interface is not None:
            self.check_device()

    def check_device(self):
        """Log hardware, firmware and radio setup; warn about anything that blocks the link."""
        log = self.get_logger()
        user = (self.interface.getMyNodeInfo() or {}).get('user', {})
        hw_model = user.get('hwModel', 'UNKNOWN')
        metadata = getattr(self.interface, 'metadata', None)
        firmware = getattr(metadata, 'firmware_version', '') or 'unknown'
        log.info(f'Connected to Meshtastic node {user.get("id")} ({user.get("longName")}), '
                 f'hardware {hw_model}, firmware {firmware}')

        if self.expected_hw_model and hw_model != self.expected_hw_model:
            log.warn(f'Expected hardware {self.expected_hw_model}, device reports {hw_model}')
        if (self.min_firmware_version and firmware != 'unknown'
                and version_tuple(firmware) < version_tuple(self.min_firmware_version)):
            log.error(f'Firmware {firmware} is older than required '
                      f'{self.min_firmware_version}; update the board')

        try:
            lora = self.interface.localNode.localConfig.lora
            region = config_pb2.Config.LoRaConfig.RegionCode.Name(lora.region)
            preset = config_pb2.Config.LoRaConfig.ModemPreset.Name(lora.modem_preset)
        except (AttributeError, ValueError) as e:
            log.warn(f'Could not read LoRa config: {e}')
            return
        log.info(f'LoRa region {region}, modem preset {preset}')
        if region == 'UNSET':
            log.error('LoRa region is UNSET, the radio will not transmit. Set it with '
                      '`meshtastic --set lora.region EU_868` (or your region)')
        if not lora.tx_enabled:
            log.error('LoRa transmit is disabled on the device (lora.tx_enabled = false)')

    def resolve_device_name(self, configured):
        """Use the configured name, else the Meshtastic node id, else the hostname."""
        if configured:
            return sanitize_name(configured)
        if self.interface is not None:
            node_id = (self.interface.getMyNodeInfo() or {}).get('user', {}).get('id')
            if node_id:
                return default_device_name(node_id)
        return sanitize_name(socket.gethostname())

    def send(self, payload, dest=None):
        if self.interface is None:
            self.get_logger().warn('Not connected, dropping packet', throttle_duration_sec=5.0)
            return
        try:
            self.interface.sendData(
                payload,
                destinationId=dest or self.dest,
                portNum=self.port_num,
                channelIndex=self.channel_index,
                hopLimit=self.hop_limit or None,
            )
        except Exception as e:
            self.get_logger().error(f'sendData failed: {e}', throttle_duration_sec=5.0)

    def _on_receive(self, packet, interface):
        if packet.get('decoded', {}).get('portnum') == self.port_name:
            self._inbox.put(('rx', packet))

    def _on_connection_lost(self, interface):
        self._inbox.put(('lost', None))

    # --- TX: local topics -> mesh ----------------------------------------

    def should_bridge(self, topic):
        if topic in self.own_topics or topic in self.unsupported:
            return False
        # Never send back out what came from other devices, even if another
        # bridge on the same ROS domain published it.
        first = topic.strip('/').split('/', 1)[0]
        if first in self.device_names.values() or (self.remote_namespace
                                                   and first == self.remote_namespace):
            return False
        return matches(topic, self.include_topics) and not matches(topic, self.exclude_topics)

    def scan_topics(self):
        """Subscribe to new local topics that pass the include/exclude filters."""
        for name, types in self.get_topic_names_and_types():
            if name in self.tx_topics or not self.should_bridge(name):
                continue
            try:
                msg_cls = get_message(types[0])
            except (AttributeError, ModuleNotFoundError, ValueError) as e:
                self.get_logger().warn(f'Not bridging {name}: cannot load {types[0]} ({e})')
                self.unsupported.add(name)
                continue
            topic = TxTopic(name, types[0], self.interval_for(name))
            topic.subscription = self.create_subscription(
                msg_cls, name, lambda data, t=topic: self.on_local_message(t, data),
                self.sub_qos, raw=True)
            self.tx_topics[name] = topic
            self.get_logger().info(
                f'Bridging {name} [{types[0]}] every >= {topic.min_interval_s}s')
            self.announce(topic)
        # TODO: drop subscriptions for topics that no longer have publishers.

    def on_local_message(self, topic, serialized):
        # Latest value wins: if messages arrive faster than the topic's
        # interval, older ones are overwritten and never sent.
        topic.latest = serialized
        self.send_if_due(topic)

    def send_if_due(self, topic):
        now = time.monotonic()
        if topic.latest is None or now - topic.last_sent < topic.min_interval_s:
            return
        serialized, topic.latest = topic.latest, None
        topic.last_sent = now
        try:
            packets = codec.encode_data(topic.topic_id, topic.seq, serialized,
                                        self.max_payload_bytes, self.max_fragments)
        except ValueError as e:
            self.get_logger().warn(f'Not sending {topic.name}: {e}', throttle_duration_sec=30.0)
            return
        topic.seq += 1
        for packet in packets:
            self.send(packet)

    def announce(self, topic):
        self.send(codec.encode_announce(
            topic.topic_id, self.device_name, topic.name, topic.msg_type))

    def announce_all(self):
        self.last_announce_all = time.monotonic()
        for topic in self.tx_topics.values():
            self.announce(topic)

    # --- RX: mesh -> local topics ----------------------------------------

    def poll(self):
        """Handle queued Meshtastic events and send rate-limited messages that are due."""
        while True:
            try:
                kind, packet = self._inbox.get_nowait()
            except queue.Empty:
                break
            if kind == 'rx':
                self.handle_packet(packet)
            elif kind == 'lost':
                self.get_logger().error('Lost connection to Meshtastic device')
                self.interface = None
                # TODO: schedule reconnect.
        for topic in self.tx_topics.values():
            self.send_if_due(topic)
        self.reassembler.expire()

    def handle_packet(self, packet):
        sender = packet.get('fromId')
        try:
            kind, fields = codec.decode(packet.get('decoded', {}).get('payload', b''))
        except ValueError as e:
            self.get_logger().debug(f'Ignoring packet from {sender}: {e}')
            return
        if not self.accept_device(sender, self.device_names.get(sender)):
            return

        if kind == codec.KIND_REQUEST:
            # Several devices may ask at once; answer at most every 10 s.
            if self.tx_enabled and time.monotonic() - self.last_announce_all > 10.0:
                self.announce_all()
        elif not self.rx_enabled:
            return
        elif kind == codec.KIND_ANNOUNCE:
            self.register_remote(sender, fields)
        elif kind == codec.KIND_DATA:
            self.handle_data(sender, fields, packet)

    def accept_device(self, sender, device=None):
        """
        Apply ``allow_devices``/``block_devices`` to a sender.

        Patterns match the Meshtastic node id (``!a1b2c3d4``) or the announced
        device name. Until a device has announced its name only the block
        list is checked, so its announcement can still arrive.
        """
        names = [sender or ''] + ([device] if device else [])
        if any(matches(name, self.block_devices) for name in names):
            return False
        return device is None or any(matches(name, self.allow_devices) for name in names)

    def accept_remote_topic(self, topic):
        return (matches(topic, self.rx_include_topics)
                and not matches(topic, self.rx_exclude_topics))

    def remote_topic_name(self, device, topic):
        parts = [self.remote_namespace, device, topic.strip('/')]
        return '/' + '/'.join(part for part in parts if part)

    def register_remote(self, sender, fields):
        key = (sender, fields['topic_id'])
        if key in self.remote_topics:
            return
        device = sanitize_name(fields['device_name']) if fields['device_name'] \
            else default_device_name(sender)
        # TODO: two devices announcing the same name end up on the same topics.
        self.device_names[sender] = device
        local_name = self.remote_topic_name(device, fields['topic'])
        if not self.accept_device(sender, device) or not self.accept_remote_topic(fields['topic']):
            # Remember the topic without a publisher so its data is dropped
            # quietly instead of triggering announce requests.
            self.get_logger().info(f'Filtered out {fields["topic"]} from {device} ({sender})')
            self.remote_topics[key] = RemoteTopic(device, fields['topic'], local_name, None, None)
            return
        try:
            msg_cls = get_message(fields['msg_type'])
        except (AttributeError, ModuleNotFoundError, ValueError):
            self.get_logger().warn(
                f'Cannot republish {local_name}: type {fields["msg_type"]} is not installed')
            self.remote_topics[key] = RemoteTopic(device, fields['topic'], local_name, None, None)
            return
        publisher = self.create_publisher(msg_cls, local_name, 10)
        self.own_topics.add(local_name)
        self.remote_topics[key] = RemoteTopic(
            device, fields['topic'], local_name, msg_cls, publisher)
        self.get_logger().info(f'Republishing {fields["topic"]} from {device} on {local_name}')

    def handle_data(self, sender, fields, packet):
        remote = self.remote_topics.get((sender, fields['topic_id']))
        if remote is None:
            self.request_announce(sender)
            return
        serialized = self.reassembler.add(sender, fields)
        if serialized is None or remote.publisher is None:
            return
        try:
            msg = deserialize_message(serialized, remote.msg_cls)
        except Exception as e:
            self.get_logger().warn(f'Could not deserialize {remote.local_name}: {e}')
            return
        self.publish_link_quality(sender, remote, fields['msg_seq'], packet)
        remote.publisher.publish(msg)

    def request_announce(self, sender):
        now = time.monotonic()
        if now - self.last_request.get(sender, 0.0) > 30.0:
            self.last_request[sender] = now
            self.send(codec.encode_request(), dest=sender)

    def publish_link_quality(self, sender, remote, msg_seq, packet):
        # protobuf omits zero-valued fields, so hopStart may be missing.
        hop_start, hop_limit = packet.get('hopStart'), packet.get('hopLimit')
        hops = '' if hop_start is None or hop_limit is None else str(hop_start - hop_limit)
        values = {
            'topic': remote.local_name,
            'source_topic': remote.source_topic,
            'msg_seq': str(msg_seq),
            'rssi': str(packet.get('rxRssi', '')),
            'snr': str(packet.get('rxSnr', '')),
            'hops': hops,
        }
        self.link_pub.publish(DiagnosticStatus(
            level=DiagnosticStatus.OK,
            name=remote.device,
            hardware_id=sender or '',
            message='rx',
            values=[KeyValue(key=k, value=v) for k, v in values.items()],
        ))

    def destroy_node(self):
        """Close the serial interface before tearing down the node."""
        if self.interface is not None:
            self.interface.close()
            self.interface = None
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node = BridgeNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == '__main__':
    main()

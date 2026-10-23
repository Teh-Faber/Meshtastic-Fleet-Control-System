"""
Wire format for carrying ROS 2 topics over a Meshtastic mesh.

Messages travel as their CDR-serialized bytes (what ROS 2 itself puts on
the wire), so any message type works without per-type code. Every packet
starts with one byte: protocol version (high nibble) and packet kind (low
nibble).

ANNOUNCE  maps a 16-bit topic id to the topic name, message type and the
          sender's device name. Sent when a topic is first bridged, on a
          timer, and when another device asks for it.
DATA      one fragment of a serialized message for a topic id.
REQUEST   asks a device to re-announce its topics. Sent when DATA arrives
          for a topic id the receiver has not seen announced.

Both ends need the message type installed and the same ROS 2 distro, since
the CDR layout of a type can change between distros.
"""

import math
import struct
import time
import zlib

VERSION = 1
KIND_ANNOUNCE = 1
KIND_DATA = 2
KIND_REQUEST = 3

# TODO: verify against the firmware version in use (DATA_PAYLOAD_LEN).
MAX_PAYLOAD_BYTES = 233
MAX_FRAGMENTS = 16

_KIND = struct.Struct('<B')
# kind byte, topic id, message seq, fragment (index << 4 | count - 1)
_DATA = struct.Struct('<BHBB')
# kind byte, topic id, then device name, topic and type as length-prefixed strings
_ANNOUNCE = struct.Struct('<BH')

DATA_HEADER_SIZE = _DATA.size


def topic_id(topic, msg_type):
    """Return the 16-bit id used on the wire for a topic name and type."""
    return zlib.crc32(f'{topic}|{msg_type}'.encode()) & 0xFFFF


def _kind_byte(kind):
    return (VERSION << 4) | kind


def _pack_str(text):
    raw = text.encode('utf-8')
    if len(raw) > 255:
        raise ValueError(f'"{text}" is longer than 255 bytes')
    return bytes([len(raw)]) + raw


def _unpack_str(buf, offset):
    length = buf[offset]
    start = offset + 1
    if start + length > len(buf):
        raise ValueError('truncated string')
    return buf[start:start + length].decode('utf-8'), start + length


def encode_announce(tid, device_name, topic, msg_type):
    """Encode an ANNOUNCE packet for one topic."""
    payload = (_ANNOUNCE.pack(_kind_byte(KIND_ANNOUNCE), tid) + _pack_str(device_name)
               + _pack_str(topic) + _pack_str(msg_type))
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError(f'announce for {topic} is {len(payload)} bytes')
    return payload


def encode_request():
    """Encode a REQUEST packet asking for a re-announce."""
    return _KIND.pack(_kind_byte(KIND_REQUEST))


def encode_data(tid, msg_seq, serialized, max_payload=MAX_PAYLOAD_BYTES,
                max_fragments=MAX_FRAGMENTS):
    """Split a serialized message into DATA packets."""
    chunk = max_payload - _DATA.size
    count = max(1, math.ceil(len(serialized) / chunk))
    if count > min(max_fragments, MAX_FRAGMENTS):
        raise ValueError(f'{len(serialized)} bytes needs {count} fragments, '
                         f'max is {min(max_fragments, MAX_FRAGMENTS)}')
    return [
        _DATA.pack(_kind_byte(KIND_DATA), tid, msg_seq & 0xFF, (i << 4) | (count - 1))
        + serialized[i * chunk:(i + 1) * chunk]
        for i in range(count)
    ]


def decode(payload):
    """Return ``(kind, fields)``. Raises ``ValueError`` on anything malformed."""
    if not payload:
        raise ValueError('empty payload')
    version, kind = payload[0] >> 4, payload[0] & 0x0F
    if version != VERSION:
        raise ValueError(f'unsupported protocol version {version}')
    try:
        if kind == KIND_DATA:
            _, tid, seq, frag = _DATA.unpack_from(payload)
            return kind, {
                'topic_id': tid,
                'msg_seq': seq,
                'index': frag >> 4,
                'count': (frag & 0x0F) + 1,
                'chunk': bytes(payload[_DATA.size:]),
            }
        if kind == KIND_ANNOUNCE:
            _, tid = _ANNOUNCE.unpack_from(payload)
            device_name, offset = _unpack_str(payload, _ANNOUNCE.size)
            topic, offset = _unpack_str(payload, offset)
            msg_type, _ = _unpack_str(payload, offset)
            return kind, {
                'topic_id': tid,
                'device_name': device_name,
                'topic': topic,
                'msg_type': msg_type,
            }
        if kind == KIND_REQUEST:
            return kind, {}
    except (struct.error, IndexError) as e:
        raise ValueError(f'truncated packet: {e}') from e
    raise ValueError(f'unknown packet kind {kind}')


class Reassembler:
    """Collect DATA fragments per (sender, topic id, message seq) into whole messages."""

    def __init__(self, timeout_s=60.0):
        self.timeout_s = timeout_s
        self._partial = {}  # key -> (t_first, count, {index: chunk})

    def add(self, sender, fields):
        """Add one fragment. Return the full message once every fragment is in, else None."""
        if fields['count'] == 1:
            return fields['chunk']
        key = (sender, fields['topic_id'], fields['msg_seq'])
        _, count, chunks = self._partial.setdefault(
            key, (time.monotonic(), fields['count'], {}))
        chunks[fields['index']] = fields['chunk']
        if len(chunks) < count:
            return None
        del self._partial[key]
        return b''.join(chunks[i] for i in range(count))

    def expire(self):
        """Drop incomplete messages older than ``timeout_s``. Return how many were dropped."""
        now = time.monotonic()
        stale = [key for key, (t_first, _, _) in self._partial.items()
                 if now - t_first > self.timeout_s]
        for key in stale:
            del self._partial[key]
        return len(stale)

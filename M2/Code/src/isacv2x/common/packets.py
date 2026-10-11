"""Wire formats for the two over-the-air messages: Beacon and Alert.

Frame layout (big-endian):

    | preamble (N) | version (1) | type (1) | length (2) | payload (length) | crc32 (4) |

* ``preamble``  identifies EV-system traffic (alert.preamble_hex in system.yaml).
* ``crc32``     covers version..payload (everything after the preamble).
* ``length``    is the payload length only.

Beacon payload  : vehicle_id u32 | seq u32 | timestamp_us u64 | segment (u8 len + ascii)
                  | offset_m f32 | speed_mps f32   (NaN = speed not reported)
Alert payload   : ev_id u16 | priority u8 | seq u32 | timestamp_us u64
                  | n_route u8 | n_route x segment (u8 len + ascii)

Beacons carry position along the segment and an optional self-reported speed so that
speed can also be estimated by differencing consecutive beacons (speed_from_beacons).
"""
from __future__ import annotations

import math
import struct
import time
import zlib
from dataclasses import dataclass, field

VERSION = 1
TYPE_BEACON = 1
TYPE_ALERT = 2

_HDR = struct.Struct(">BBH")          # version, type, length
_CRC = struct.Struct(">I")
_BEACON_FIXED = struct.Struct(">IIQ")  # vehicle_id, seq, timestamp_us
_BEACON_TAIL = struct.Struct(">ff")    # offset_m, speed_mps
_ALERT_FIXED = struct.Struct(">HBIQ")  # ev_id, priority, seq, timestamp_us


# ------------------------------------------------------------------- errors
class PacketError(ValueError):
    """Base class for all decode failures."""


class NoPreamble(PacketError):
    """Buffer does not start with the expected preamble."""


class Truncated(PacketError):
    """Buffer is shorter than its header/length claims."""


class BadCRC(PacketError):
    """Checksum mismatch (corrupted in transit)."""


class UnsupportedVersion(PacketError):
    """Frame version is not understood."""


class UnknownType(PacketError):
    """Frame type is not Beacon or Alert."""


# ------------------------------------------------------------------ helpers
def now_us() -> int:
    return time.time_ns() // 1000


def _enc_str(s: str) -> bytes:
    b = s.encode("ascii")
    if not 1 <= len(b) <= 255:
        raise PacketError(f"segment id must be 1-255 ASCII bytes, got {s!r}")
    return bytes([len(b)]) + b


def _dec_str(buf: bytes, pos: int) -> tuple[str, int]:
    if pos >= len(buf):
        raise Truncated("string length byte missing")
    n = buf[pos]
    end = pos + 1 + n
    if n == 0 or end > len(buf):
        raise Truncated("string body missing or empty")
    return buf[pos + 1:end].decode("ascii"), end


# ------------------------------------------------------------------ messages
@dataclass(frozen=True)
class Beacon:
    vehicle_id: int
    seq: int
    segment: str
    offset_m: float                       # distance travelled along the segment
    speed_mps: float = math.nan           # NaN if the vehicle doesn't report speed
    timestamp_us: int = field(default_factory=now_us)

    @property
    def has_speed(self) -> bool:
        return not math.isnan(self.speed_mps)


@dataclass(frozen=True)
class Alert:
    ev_id: int
    priority: int
    seq: int
    route: tuple[str, ...]                # segment ids the EV will traverse, in order
    timestamp_us: int = field(default_factory=now_us)


Message = Beacon | Alert


# ------------------------------------------------------------------ encoding
def _frame(preamble: bytes, mtype: int, payload: bytes) -> bytes:
    if len(payload) > 0xFFFF:
        raise PacketError("payload too large")
    body = _HDR.pack(VERSION, mtype, len(payload)) + payload
    return preamble + body + _CRC.pack(zlib.crc32(body))


def encode(msg: Message, preamble: bytes, max_bytes: int | None = None) -> bytes:
    """Serialise a Beacon or Alert into a framed byte string."""
    if isinstance(msg, Beacon):
        payload = (_BEACON_FIXED.pack(msg.vehicle_id, msg.seq, msg.timestamp_us)
                   + _enc_str(msg.segment)
                   + _BEACON_TAIL.pack(msg.offset_m, msg.speed_mps))
        mtype = TYPE_BEACON
    elif isinstance(msg, Alert):
        if not msg.route or len(msg.route) > 255:
            raise PacketError("alert route must contain 1-255 segments")
        payload = (_ALERT_FIXED.pack(msg.ev_id, msg.priority, msg.seq, msg.timestamp_us)
                   + bytes([len(msg.route)])
                   + b"".join(_enc_str(s) for s in msg.route))
        mtype = TYPE_ALERT
    else:
        raise TypeError(f"cannot encode {type(msg).__name__}")
    data = _frame(preamble, mtype, payload)
    if max_bytes is not None and len(data) > max_bytes:
        raise PacketError(f"frame is {len(data)} B, exceeds limit {max_bytes} B")
    return data


# ------------------------------------------------------------------ decoding
def peek_type(data: bytes, preamble: bytes) -> int | None:
    """Header-only check used for EV signal identification.

    Returns the message type if the buffer starts with the preamble and has a plausible
    header, else None. Does NOT verify the CRC, so a corrupted frame can still be
    "identified" -- this is what the false-positive/false-negative evaluation measures.
    """
    n = len(preamble)
    if not data.startswith(preamble) or len(data) < n + _HDR.size:
        return None
    version, mtype, _ = _HDR.unpack_from(data, n)
    if version != VERSION or mtype not in (TYPE_BEACON, TYPE_ALERT):
        return None
    return mtype


def decode(data: bytes, preamble: bytes) -> Message:
    """Parse and fully validate one frame. Raises a PacketError subclass on failure."""
    n = len(preamble)
    if not data.startswith(preamble):
        raise NoPreamble("preamble mismatch")
    if len(data) < n + _HDR.size + _CRC.size:
        raise Truncated("frame shorter than header + CRC")
    version, mtype, length = _HDR.unpack_from(data, n)
    end = n + _HDR.size + length
    if len(data) < end + _CRC.size:
        raise Truncated(f"frame claims {length} B payload, buffer too short")
    body = data[n:end]
    (crc,) = _CRC.unpack_from(data, end)
    if zlib.crc32(body) != crc:
        raise BadCRC("CRC32 mismatch")
    if version != VERSION:
        raise UnsupportedVersion(f"version {version}")
    payload = data[n + _HDR.size:end]

    try:
        if mtype == TYPE_BEACON:
            vid, seq, ts = _BEACON_FIXED.unpack_from(payload, 0)
            seg, pos = _dec_str(payload, _BEACON_FIXED.size)
            off, spd = _BEACON_TAIL.unpack_from(payload, pos)
            if pos + _BEACON_TAIL.size != len(payload):
                raise Truncated("trailing bytes in beacon payload")
            return Beacon(vid, seq, seg, off, spd, ts)
        if mtype == TYPE_ALERT:
            ev, pri, seq, ts = _ALERT_FIXED.unpack_from(payload, 0)
            pos = _ALERT_FIXED.size
            if pos >= len(payload):
                raise Truncated("route count missing")
            count, pos = payload[pos], pos + 1
            route = []
            for _ in range(count):
                s, pos = _dec_str(payload, pos)
                route.append(s)
            if pos != len(payload):
                raise Truncated("trailing bytes in alert payload")
            return Alert(ev, pri, seq, tuple(route), ts)
    except struct.error as e:
        raise Truncated(str(e)) from e
    raise UnknownType(f"type {mtype}")


# -------------------------------------------------------------- speed helper
def speed_from_beacons(prev: Beacon, curr: Beacon) -> float | None:
    """Estimate speed (m/s, >= 0) from two consecutive beacons of one vehicle.

    Only defined when both beacons are on the same segment and ``curr`` is later;
    otherwise returns None (a segment change needs topology, handled by the caller).
    The result is the along-road speed magnitude.
    """
    if prev.vehicle_id != curr.vehicle_id or prev.segment != curr.segment:
        return None
    dt = (curr.timestamp_us - prev.timestamp_us) / 1e6
    if dt <= 0:
        return None
    return abs(curr.offset_m - prev.offset_m) / dt

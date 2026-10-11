import math

import pytest

from isacv2x.common import packets as P

PRE = bytes.fromhex("A5C3E17B")


def test_beacon_roundtrip():
    b = P.Beacon(vehicle_id=42, seq=7, segment="S12", offset_m=123.5,
                 speed_mps=13.25, timestamp_us=1_700_000_000_000_000)
    out = P.decode(P.encode(b, PRE), PRE)
    assert out == b and out.has_speed


def test_beacon_without_speed():
    b = P.Beacon(1, 1, "S23", 0.0, timestamp_us=5)
    out = P.decode(P.encode(b, PRE), PRE)
    assert not out.has_speed and math.isnan(out.speed_mps)


def test_alert_roundtrip():
    a = P.Alert(ev_id=1, priority=7, seq=9, route=("S12", "S25", "S56"),
                timestamp_us=123)
    assert P.decode(P.encode(a, PRE), PRE) == a


def test_wrong_preamble():
    data = P.encode(P.Alert(1, 7, 1, ("S12",)), PRE)
    with pytest.raises(P.NoPreamble):
        P.decode(data, bytes.fromhex("DEADBEEF"))
    assert P.peek_type(data, bytes.fromhex("DEADBEEF")) is None


def test_crc_detects_corruption():
    data = bytearray(P.encode(P.Alert(1, 7, 1, ("S12", "S25")), PRE))
    data[len(PRE) + 6] ^= 0x01
    with pytest.raises(P.BadCRC):
        P.decode(bytes(data), PRE)
    # header-only identification still fires: this is the signal-ID FP path
    assert P.peek_type(bytes(data), PRE) == P.TYPE_ALERT


def test_truncation():
    data = P.encode(P.Beacon(1, 1, "S12", 1.0), PRE)
    for cut in (3, len(PRE) + 2, len(data) - 1):
        with pytest.raises(P.PacketError):
            P.decode(data[:cut], PRE)


def test_unsupported_version_needs_valid_crc():
    data = bytearray(P.encode(P.Beacon(1, 1, "S12", 1.0), PRE))
    data[len(PRE)] = 9                      # version byte -> CRC now wrong
    with pytest.raises(P.BadCRC):
        P.decode(bytes(data), PRE)


def test_max_bytes_enforced():
    a = P.Alert(1, 1, 1, tuple(f"S{i}" for i in range(100)))
    with pytest.raises(P.PacketError):
        P.encode(a, PRE, max_bytes=64)


def test_bad_inputs():
    with pytest.raises(P.PacketError):
        P.encode(P.Alert(1, 1, 1, ()), PRE)
    with pytest.raises(P.PacketError):
        P.encode(P.Beacon(1, 1, "", 0.0), PRE)


def test_default_alert_fits_config_limit():
    # alert.max_payload_bytes = 256 in system.yaml
    a = P.Alert(1, 7, 1, ("S12", "S25", "S56", "S36", "S23"))
    assert len(P.encode(a, PRE, max_bytes=256)) < 64


def test_speed_from_beacons():
    a = P.Beacon(5, 1, "S12", 10.0, timestamp_us=1_000_000)
    b = P.Beacon(5, 2, "S12", 25.0, timestamp_us=2_000_000)
    assert P.speed_from_beacons(a, b) == pytest.approx(15.0)
    assert P.speed_from_beacons(b, a) is None            # time goes backwards
    other_seg = P.Beacon(5, 3, "S23", 1.0, timestamp_us=3_000_000)
    assert P.speed_from_beacons(b, other_seg) is None    # segment change
    other_veh = P.Beacon(6, 2, "S12", 25.0, timestamp_us=2_000_000)
    assert P.speed_from_beacons(a, other_veh) is None

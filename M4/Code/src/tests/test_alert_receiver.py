import pytest

from isacv2x.alert.receiver import AlertReceiver
from isacv2x.backends.loopback import LoopbackChannel
from isacv2x.common import packets as P
from isacv2x.common.config import load_config
from isacv2x.common.logging_utils import EventLogger

cfg = load_config("config/system.yaml")
PRE = cfg.alert.preamble


def make(segment="S23", **kw):
    ch = LoopbackChannel()
    return ch.sender(), AlertReceiver(cfg, ch.receiver(), "RX1", segment=segment, **kw)


def alert(seq=1, route=("S12", "S23"), ts=1_000_000):
    return P.encode(P.Alert(1, 7, seq, route, ts), PRE)


def test_valid_alert_yields_event_with_latency():
    tx, rx = make(now_us=lambda: 1_250_000)
    tx.send(alert(ts=1_000_000))
    out = rx.poll(0.0)
    assert out.reason == "ok" and out.event.on_route
    assert out.event.alert.seq == 1 and out.event.node_id == "RX1"
    assert out.event.latency_s == pytest.approx(0.25)


def test_repeat_copies_are_duplicates():
    tx, rx = make()
    for _ in range(3):
        tx.send(alert(seq=5))
    tx.send(alert(seq=6))
    assert [o.reason for o in rx.drain()] == ["ok", "duplicate", "duplicate", "ok"]
    assert rx.stats["ok"] == 2 and rx.stats["duplicate"] == 2


def test_off_route_alert_is_reported_but_flagged():
    tx, rx = make(segment="S56")
    tx.send(alert(route=("S12", "S23")))
    ev = rx.poll(0.0).event
    assert ev is not None and ev.on_route is False


def test_no_segment_means_always_on_route():
    tx, rx = make(segment=None)
    tx.send(alert())
    assert rx.poll(0.0).event.on_route


def test_non_ev_traffic_is_ignored():
    tx, rx = make()
    tx.send(b"hello world, not an EV frame")
    tx.send(P.encode(P.Alert(1, 7, 1, ("S23",)), b"\xde\xad\xbe\xef"))   # wrong preamble
    assert [o.reason for o in rx.drain()] == ["not_ev", "not_ev"]


def test_corrupt_frame_is_identified_but_rejected():
    tx, rx = make()
    bad = bytearray(alert())
    bad[len(PRE) + 6] ^= 0x01
    tx.send(bytes(bad))
    assert rx.poll(0.0).reason == "corrupt"
    assert rx.poll(0.0) is None


def test_beacon_is_not_alert():
    tx, rx = make()
    tx.send(P.encode(P.Beacon(1, 1, "S23", 1.0), PRE))
    assert rx.poll(0.0).reason == "not_alert"


def test_corrupt_copy_does_not_poison_dedup():
    tx, rx = make()
    good = alert(seq=9)
    bad = bytearray(good)
    bad[-1] ^= 0xFF
    tx.send(bytes(bad))
    tx.send(good)
    assert [o.reason for o in rx.drain()] == ["corrupt", "ok"]


def test_dedup_window_forgets_oldest():
    tx, rx = make(dedup_size=2)
    for s in (1, 2, 3):
        tx.send(alert(seq=s))
    tx.send(alert(seq=1))                         # evicted from the window -> new again
    assert [o.reason for o in rx.drain()] == ["ok", "ok", "ok", "ok"]


def test_logging_and_validation():
    log = EventLogger()
    tx, rx = make(logger=log)
    tx.send(alert(seq=2))
    rx.poll(0.0)
    ev = log.events("alert_rx")[0]
    assert ev["node"] == "RX1" and ev["reason"] == "ok" and ev["seq"] == 2
    with pytest.raises(KeyError):
        AlertReceiver(cfg, LoopbackChannel().receiver(), "RX", segment="S99")
    with pytest.raises(ValueError):
        AlertReceiver(cfg, LoopbackChannel().receiver(), "RX", dedup_size=0)

import pytest

from isacv2x.alert.relay import Relay
from isacv2x.backends.loopback import LoopbackChannel
from isacv2x.common import packets as P
from isacv2x.common.config import load_config
from isacv2x.common.logging_utils import EventLogger
from isacv2x.density.beacon_counter import BeaconCounter

cfg = load_config("config/system.yaml")
PRE = cfg.alert.preamble


def make(node="RLY1", n_out=3, **kw):
    inp = LoopbackChannel()
    outs = [LoopbackChannel() for _ in range(n_out)]
    relay = Relay(cfg, node, inp.receiver(), [o.sender() for o in outs], **kw)
    return inp.sender(), relay, [o.receiver() for o in outs]


def alert_frame(route=("S12", "S23"), seq=1):
    return P.encode(P.Alert(1, 7, seq, route, 123), PRE)


def test_forwards_alert_verbatim_to_all_outputs():
    tx, relay, rxs = make("RLY1")                       # RLY1 sits on S23
    frame = alert_frame()
    tx.send(frame)
    out = relay.step(0.0)
    assert out.reason == "forwarded" and out.delivered_to == 3
    assert [r.recv(0.0).data for r in rxs] == [frame] * 3


def test_does_not_forward_alert_for_other_routes():
    tx, relay, rxs = make("RLY1")
    tx.send(alert_frame(route=("S12", "S25")))
    assert relay.step(0.0).reason == "off_route"
    assert all(r.recv(0.0) is None for r in rxs)


def test_route_filter_can_be_disabled():
    tx, relay, rxs = make("RLY1", route_filter=False)
    tx.send(alert_frame(route=("S12", "S25")))
    assert relay.step(0.0).reason == "forwarded"


def test_every_repeat_copy_is_forwarded():
    tx, relay, rxs = make("RLY1", n_out=1)
    for _ in range(3):
        tx.send(alert_frame(seq=4))
    relay.drain()
    assert relay.stats["forwarded"] == 3
    assert sum(1 for _ in iter(lambda: rxs[0].recv(0.0), None)) == 3


def test_corrupt_and_foreign_frames_are_not_propagated():
    tx, relay, rxs = make("RLY1")
    bad = bytearray(alert_frame())
    bad[-2] ^= 0x10
    tx.send(bytes(bad))
    tx.send(b"random bytes")
    assert [o.reason for o in relay.drain()] == ["invalid", "ignored"]
    assert all(r.recv(0.0) is None for r in rxs)


def test_beacons_go_to_the_density_sink_not_to_outputs():
    sink = BeaconCounter(cfg)
    tx, relay, rxs = make("RLY1", beacon_sink=sink)       # S23 is a covered segment
    tx.send(P.encode(P.Beacon(11, 1, "S23", 5.0, timestamp_us=100), PRE))
    out = relay.step(0.0)
    assert out.reason == "beacon" and out.delivered_to == 0
    assert all(r.recv(0.0) is None for r in rxs)
    import time
    assert sink.estimate(time.monotonic()).counts["S23"] == 1.0


def test_beacon_without_sink_is_just_counted():
    tx, relay, _ = make("RLY1")
    tx.send(P.encode(P.Beacon(1, 1, "S23", 0.0), PRE))
    assert relay.step(0.0).reason == "beacon"


def test_failing_output_does_not_block_others():
    inp = LoopbackChannel()
    good, tiny = LoopbackChannel(), LoopbackChannel(max_bytes=4)   # rejects the frame
    relay = Relay(cfg, "RLY1", inp.receiver(), [tiny.sender(), good.sender()])
    inp.sender().send(alert_frame())
    out = relay.step(0.0)
    assert out.delivered_to == 1 and relay.stats["send_errors"] == 1
    assert good.receiver().recv(0.0) is not None


def test_timeout_and_validation_and_logging():
    log = EventLogger()
    tx, relay, _ = make("RLY2", logger=log)               # RLY2 sits on S25
    assert relay.step(0.01) is None
    tx.send(alert_frame(route=("S25",)))
    relay.step(0.0)
    assert log.events("relay")[0]["node"] == "RLY2" and relay.segment == "S25"
    with pytest.raises(KeyError):
        Relay(cfg, "NOPE", LoopbackChannel().receiver(), [])

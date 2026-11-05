import pytest
import yaml

from isacv2x.alert.transmitter import AlertTransmitter
from isacv2x.backends.loopback import LoopbackChannel
from isacv2x.common import packets as P
from isacv2x.common.config import load_config, parse_config
from isacv2x.common.logging_utils import EventLogger
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.astar import astar

CFG = "config/system.yaml"


def setup(repeat=None, **kw):
    raw = yaml.safe_load(open(CFG))
    if repeat is not None:
        raw["alert"]["repeat"] = repeat
    cfg = parse_config(raw)
    ch = LoopbackChannel()
    sleeps = []
    tx = AlertTransmitter(cfg, ch.sender(), sleep=sleeps.append, **kw)
    return cfg, ch, tx, sleeps


def drain(rx):
    out = []
    while (d := rx.recv(0.0)) is not None:
        out.append(d.data)
    return out


def test_sends_configured_number_of_identical_copies():
    cfg, ch, tx, sleeps = setup(repeat=3)
    sent = tx.send_alert(["S12", "S23"])
    frames = drain(ch.receiver())
    assert len(frames) == 3 and len(set(frames)) == 1 and sent.copies == 3
    assert frames[0] == sent.frame
    assert sleeps == [cfg.alert.repeat_gap_s] * 2        # gaps between copies only


def test_frame_decodes_to_expected_alert():
    cfg, ch, tx, _ = setup()
    tx.send_alert(("S12", "S25", "S56"))
    msg = P.decode(drain(ch.receiver())[0], cfg.alert.preamble)
    assert isinstance(msg, P.Alert)
    assert (msg.ev_id, msg.priority, msg.seq) == (cfg.alert.ev_id, cfg.alert.priority, 1)
    assert msg.route == ("S12", "S25", "S56")


def test_sequence_increments_per_alert_not_per_copy():
    cfg, ch, tx, _ = setup(repeat=2)
    tx.send_alert(["S12"])
    tx.send_alert(["S12"])
    seqs = [P.decode(f, cfg.alert.preamble).seq for f in drain(ch.receiver())]
    assert seqs == [1, 1, 2, 2]


def test_sequence_wraps_without_hitting_zero():
    cfg, ch, tx, _ = setup(repeat=1)
    tx._seq = 0xFFFFFFFF
    tx.send_alert(["S12"])
    assert P.decode(drain(ch.receiver())[0], cfg.alert.preamble).seq == 1


def test_accepts_route_object_from_planner():
    cfg, ch, tx, _ = setup(repeat=1)
    route = astar(RoadGraph(cfg), "J1", "J3")
    tx.send_alert(route)
    assert P.decode(drain(ch.receiver())[0], cfg.alert.preamble).route == route.segments


def test_logs_each_copy():
    log = EventLogger()
    cfg, ch, tx, _ = setup(repeat=3, logger=log)
    tx.send_alert(["S12", "S23"])
    ev = log.events("alert_tx")
    assert [e["copy"] for e in ev] == [1, 2, 3]
    assert ev[0]["route"] == ["S12", "S23"] and ev[0]["seq"] == 1 and ev[0]["sent_us"] > 0


def test_rejects_empty_route_and_oversized_frame():
    cfg, ch, tx, _ = setup()
    with pytest.raises(P.PacketError):
        tx.send_alert([])
    with pytest.raises(P.PacketError):
        tx.send_alert([f"SEG{i}" for i in range(200)])    # exceeds alert.max_payload_bytes
    assert drain(ch.receiver()) == []                     # nothing partial was sent

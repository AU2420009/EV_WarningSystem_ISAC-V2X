import pytest
import yaml

from isacv2x.common import packets as P
from isacv2x.common.config import load_config, parse_config
from isacv2x.density.base import feed
from isacv2x.density.beacon_counter import BeaconCounter
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.replanner import Replanner

CFG = "config/system.yaml"


def make_cfg(alpha=1.0, ttl=2.0):
    raw = yaml.safe_load(open(CFG))
    raw["density"]["smoothing_alpha"] = alpha
    raw["density"]["beacon_ttl_s"] = ttl
    return parse_config(raw)


def bc(**kw):
    cfg = make_cfg(**kw)
    return BeaconCounter(cfg), cfg


def beacon(vid, seg="S12", ts=1_000_000, off=0.0, spd=float("nan"), seq=1):
    return P.Beacon(vid, seq, seg, off, spd, ts)


def test_counts_unique_vehicles_per_segment_covered_only():
    c, _ = bc()
    for vid in (1, 2, 3):
        c.ingest(beacon(vid, "S12"), rx_time=0.0, node_id="RSU1")
    c.ingest(beacon(4, "S23"), rx_time=0.0)
    est = c.estimate(0.5)
    assert est.counts == {"S12": 3.0, "S23": 1.0, "S45": 0.0, "S25": 0.0}
    assert est.source == "beacon_counter"


def test_same_vehicle_heard_by_two_nodes_counts_once():
    c, _ = bc()
    assert c.ingest(beacon(1, ts=100), 0.0, "RSU1") == "ok"
    assert c.ingest(beacon(1, ts=100), 0.1, "RLY1") == "stale"     # same beacon, 2nd node
    assert c.estimate(0.2).counts["S12"] == 1.0


def test_ttl_boundary():
    c, _ = bc(ttl=2.0)
    c.ingest(beacon(1), rx_time=10.0)
    assert c.estimate(12.0).counts["S12"] == 1.0         # exactly ttl: still active
    assert c.estimate(12.01).counts["S12"] == 0.0        # beyond ttl: gone
    assert c.active_vehicles(12.01) == {}


def test_vehicle_changing_segment_moves_its_count():
    c, _ = bc()
    c.ingest(beacon(1, "S12", ts=100), 0.0)
    c.ingest(beacon(1, "S23", ts=200), 0.5)
    est = c.estimate(0.6)
    assert est.counts["S12"] == 0.0 and est.counts["S23"] == 1.0


def test_out_of_order_beacon_ignored():
    c, _ = bc()
    c.ingest(beacon(1, "S23", ts=200), 0.0)
    assert c.ingest(beacon(1, "S12", ts=100), 0.1) == "stale"
    assert c.estimate(0.2).counts["S23"] == 1.0
    assert c.stats["stale"] == 1


def test_unknown_and_uncovered_segments():
    c, _ = bc()
    assert c.ingest(beacon(1, "S99"), 0.0) == "unknown_segment"
    assert c.ingest(beacon(2, "S56"), 0.0) == "ok"        # real segment, no sensing node
    est = c.estimate(0.1)
    assert "S56" not in est.counts and sum(est.counts.values()) == 0
    assert c.stats["unknown_segment"] == 1 and c.stats["uncovered_segment"] == 1


def test_ingest_bytes_paths():
    c, cfg = bc()
    pre = cfg.alert.preamble
    good = P.encode(beacon(7, "S12", ts=5), pre)
    alert = P.encode(P.Alert(1, 7, 1, ("S12",)), pre)
    corrupt = bytearray(good)
    corrupt[-1] ^= 0xFF
    assert c.ingest_bytes(good, 0.0, "RSU1") == "ok"
    assert c.ingest_bytes(alert, 0.0) == "not_beacon"
    assert c.ingest_bytes(bytes(corrupt), 0.0) == "bad_frame"
    assert c.ingest_bytes(b"garbage", 0.0) == "bad_frame"
    assert c.ingest_bytes(P.encode(beacon(8, ts=5), b"\xde\xad"), 0.0) == "bad_frame"
    assert c.estimate(0.1).counts["S12"] == 1.0
    assert c.stats["bad_frame"] == 3 and c.stats["not_beacon"] == 1


def test_mean_speed_reported_and_derived():
    c, _ = bc()
    c.ingest(beacon(1, "S12", ts=1_000_000, spd=10.0), 0.0)
    # vehicle 2 reports no speed; derive from two beacons 1 s apart, 14 m travelled
    c.ingest(beacon(2, "S12", ts=1_000_000, off=100.0), 0.0)
    c.ingest(beacon(2, "S12", ts=2_000_000, off=114.0), 1.0)
    c.ingest(beacon(3, "S23", ts=1_000_000), 1.0)          # no speed info at all
    est = c.estimate(1.0)
    assert est.mean_speed_mps["S12"] == pytest.approx(12.0)      # (10 + 14) / 2
    assert "S23" not in est.mean_speed_mps


def test_smoothing_blends_successive_estimates():
    c, _ = bc(alpha=0.5)
    c.ingest(beacon(1), 0.0)
    c.ingest(beacon(2, ts=2), 0.0)
    assert c.estimate(0.0).counts["S12"] == 2.0
    assert c.estimate(5.0).counts["S12"] == pytest.approx(1.0)   # raw 0 -> 0.5*0+0.5*2


def test_ttl_validation():
    with pytest.raises(ValueError):
        BeaconCounter(make_cfg(), ttl_s=0)


def test_congested_segment_reroutes_ev():
    c, cfg = bc()
    rp = Replanner(RoadGraph(cfg), cfg.routing, "J1", "J3")
    _, clear = feed(c, rp, 0.0)
    assert clear.route.segments == ("S12", "S23")
    for vid in range(20):                                  # S12 capacity is 20
        c.ingest(beacon(vid, "S12", ts=10 + vid), 1.0, "RSU1")
    _, jam = feed(c, rp, 1.0)
    assert jam.replanned and jam.route.segments != ("S12", "S23")

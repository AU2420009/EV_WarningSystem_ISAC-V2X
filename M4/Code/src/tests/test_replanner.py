import dataclasses

import pytest
import yaml

from isacv2x.common.config import load_config, parse_config
from isacv2x.common.logging_utils import EventLogger
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.replanner import Replanner

CFG = "config/system.yaml"


def make(src="J1", dst="J3", clock=None, logger=None, mutate=None):
    if mutate is None:
        cfg = load_config(CFG)
    else:
        raw = yaml.safe_load(open(CFG))
        mutate(raw)
        cfg = parse_config(raw)
    kw = {"logger": logger}
    if clock is not None:
        kw["clock"] = clock
    return Replanner(RoadGraph(cfg), cfg.routing, src, dst, **kw), cfg


def test_first_update_always_plans():
    rp, _ = make()
    r = rp.update_counts({})
    assert r.replanned and r.reason == "initial" and r.route.segments == ("S12", "S23")
    assert r.route_changed                       # None -> first route


def test_small_change_is_ignored():
    rp, cfg = make()
    rp.update_counts({})
    # 1 vehicle on a 20-capacity segment = 0.05 < replan_min_change (0.1)
    r = rp.update_counts({"S12": 1})
    assert not r.replanned and r.reason == "below_threshold"
    assert r.route.segments == ("S12", "S23")
    assert r.max_change == pytest.approx(0.05)


def test_change_accumulates_against_last_plan_not_last_update():
    rp, _ = make()
    rp.update_counts({})
    rp.update_counts({"S12": 1})                 # 0.05, ignored
    r = rp.update_counts({"S12": 3})             # 0.15 vs. the *planned* 0.0 -> replan
    assert r.replanned and r.reason == "density_change"


def test_big_change_reroutes():
    rp, _ = make()
    rp.update_counts({})
    r = rp.update_counts({"S12": 20, "S23": 20})
    assert r.replanned and r.route_changed
    assert r.route.segments != ("S12", "S23")
    assert rp.route is r.route


def test_replan_without_route_change_is_reported():
    rp, _ = make()
    rp.update_counts({})
    r = rp.update_counts({"S14": 15})            # irrelevant, off the chosen path
    assert r.replanned and not r.route_changed


def test_force_and_set_trip():
    rp, _ = make()
    rp.update_counts({})
    assert rp.update_counts({}, force=True).reason == "forced"
    r = rp.set_trip("J1", "J6")
    assert r.replanned and r.route.junctions[-1] == "J6"


def test_deadline_measured_with_injected_clock():
    ticks = iter([0.0, 0.2, 10.0, 11.0])         # 0.2 s then 1.0 s per replan
    rp, cfg = make(clock=lambda: next(ticks))
    a = rp.update_counts({})
    assert a.compute_s == pytest.approx(0.2) and a.deadline_met    # deadline 0.5 s
    b = rp.update_counts({"S12": 20, "S23": 20})
    assert b.compute_s == pytest.approx(1.0) and not b.deadline_met
    s = rp.stats()
    assert s["replans"] == 2 and s["deadline_miss"] == 1
    assert s["max_compute_s"] == pytest.approx(1.0)


def test_no_route_is_reported_not_raised():
    def disconnect(raw):
        raw["junctions"]["J7"] = {"x": 5000, "y": 5000}
    rp, _ = make(src="J1", dst="J3", mutate=disconnect)
    rp.update_counts({})
    r = rp.set_trip("J1", "J7")
    assert r.route is None and r.reason == "no_route" and rp.route is None


def test_events_are_logged():
    log = EventLogger()
    rp, _ = make(logger=log)
    rp.update_counts({})
    rp.update_counts({"S12": 1})
    rp.update_counts({"S12": 20, "S23": 20})
    ev = log.events("replan")
    assert [e["reason"] for e in ev] == ["initial", "below_threshold", "density_change"]
    assert ev[0]["route"] == ["S12", "S23"] and ev[1]["route"] == ["S12", "S23"]
    assert ev[2]["route_changed"] is True and ev[2]["algorithm"] == "astar"


def test_unknown_trip_endpoint():
    cfg = load_config(CFG)
    with pytest.raises(KeyError):
        Replanner(RoadGraph(cfg), cfg.routing, "J1", "NOPE")

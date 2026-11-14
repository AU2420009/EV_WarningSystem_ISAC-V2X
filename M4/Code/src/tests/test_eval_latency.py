import pytest

from isacv2x.eval.latency import compute_latency


def rx(lat, reason="ok", on_route=True):
    return {"event": "alert_rx", "reason": reason, "on_route": on_route, "latency_s": lat}


def test_latency_report():
    ev = [rx(0.01), rx(0.02), rx(0.03), rx(9.0, reason="duplicate"), rx(5.0, on_route=False)]
    r = compute_latency(ev, 0.1)
    assert r.samples == 3 and r.p50_s == 0.02 and r.max_s == 0.03 and r.meets_target
    assert not compute_latency([rx(0.5)], 0.1).meets_target
    assert not compute_latency([], 0.1).meets_target


def test_negative_latency_means_clock_skew_and_fails():
    r = compute_latency([rx(0.01), rx(-0.2)], 0.1)
    assert r.negative == 1 and not r.meets_target


def test_time_saved_prefers_adaptive_route_under_congestion():
    from isacv2x.common.config import load_config
    from isacv2x.eval.latency import compute_time_saved
    cfg = load_config("config/system.yaml")
    base = ("S12", "S25", "S56")          # not necessarily the baseline; compare to computed one
    ev = [{"event": "density", "counts": {"S12": 18.0, "S23": 2.0, "S25": 1.0, "S45": 4.0}},
          {"event": "replan", "replanned": True, "route": ["S14", "S45", "S56"]},
          {"event": "alert_tx", "copy": 1}]
    r = compute_time_saved(ev, cfg, "J1", "J6")
    assert r.decisions == 1 and r.worse_decisions == 0 and r.saved_s >= 0 and r.meets_target
    bad = [ev[0], {"event": "replan", "replanned": True, "route": ["S12", "S23", "S36"]},
           ev[2]]
    r2 = compute_time_saved(bad, cfg, "J1", "J6")
    assert r2.worse_decisions == 1 and not r2.meets_target
    assert not compute_time_saved([], cfg, "J1", "J6").meets_target

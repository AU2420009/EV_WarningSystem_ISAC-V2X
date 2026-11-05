import math

import pytest

from isacv2x.common.config import load_config
from isacv2x.eval.replan import benchmark_planners, compute_replan_report
from isacv2x.eval.stats import percentile


def rp(t, changed=False, reason="density_change", replanned=True):
    return {"event": "replan", "replanned": replanned, "route_changed": changed,
            "reason": reason, "compute_s": t}


def test_percentile_nearest_rank():
    assert percentile([1, 2, 3, 4], 50) == 2 and percentile([1, 2, 3, 4], 95) == 4
    assert percentile([5], 0) == 5 and math.isnan(percentile([], 50))
    with pytest.raises(ValueError):
        percentile([1], 101)


def test_replan_report_pass():
    ev = [rp(0.001, True, "initial"), rp(0.002, True), rp(0, replanned=False)]
    r = compute_replan_report(ev, 0.5)
    assert r.replans == 2 and r.route_changes == 1 and r.skipped == 1
    assert r.meets_target and r.max_s == 0.002 and r.deadline_misses == 0


def test_replan_report_needs_adaptation_and_no_misses():
    only_initial = compute_replan_report([rp(0.001, True, "initial")], 0.5)
    assert not only_initial.meets_target                       # route never adapted
    slow = compute_replan_report([rp(0.9, True, "initial"), rp(0.8, True)], 0.5)
    assert slow.deadline_misses == 2 and not slow.meets_target
    assert not compute_replan_report([], 0.5).meets_target


def test_planner_benchmark_agrees_and_is_fast():
    cfg = load_config("config/system.yaml")
    res, mismatch = benchmark_planners(cfg, trials=100)
    assert mismatch == 0
    assert res["astar"].mean_expanded <= res["dijkstra"].mean_expanded + 1e-9
    assert res["dijkstra"].max_s < cfg.routing.replan_deadline_s

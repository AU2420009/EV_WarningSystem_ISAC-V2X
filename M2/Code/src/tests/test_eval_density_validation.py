import math

import pytest

from isacv2x.eval.density_validation import compute_density_report


def make(scenario, truth, est_fn, n=10, t0=0.0):
    ev = []
    for i in range(n):
        t = t0 + i
        ev.append({"event": "truth", "scenario": scenario, "at": t, "counts": dict(truth)})
        ev.append({"event": "density", "at": t, "counts": est_fn(i)})
    return ev


TRUTH = {"S12": 10.0, "S23": 2.0}


def test_exact_estimates_pass_with_two_scenarios():
    ev = make("a", TRUTH, lambda i: dict(TRUTH)) + make("b", TRUTH, lambda i: dict(TRUTH), t0=100)
    r = compute_density_report(ev)
    assert r.meets_target and r.mae == 0 and len(r.scenarios) == 2


def test_one_scenario_is_not_enough():
    r = compute_density_report(make("a", TRUTH, lambda i: dict(TRUTH)))
    assert r.scenarios[0].passed and not r.meets_target


def test_warmup_is_discarded():
    # bad estimate for the first half, perfect afterwards
    est = lambda i: {"S12": 0.0, "S23": 0.0} if i < 5 else dict(TRUTH)
    r = compute_density_report(make("a", TRUTH, est), warmup_frac=0.5, min_scenarios=1)
    assert r.meets_target and r.scenarios[0].samples == 5
    r2 = compute_density_report(make("a", TRUTH, est), warmup_frac=0.0, min_scenarios=1)
    assert not r2.meets_target and r2.scenarios[0].mae == pytest.approx(3.0)


def test_wrong_busiest_segment_fails_even_with_small_mae():
    est = lambda i: {"S12": 6.0, "S23": 6.5}           # MAE 2.25 but also wrong ordering
    r = compute_density_report(make("a", TRUTH, est), mae_tol=10, min_scenarios=1)
    assert not r.scenarios[0].busiest_ok and not r.meets_target


def test_uncovered_segments_are_ignored_and_errors_computed():
    truth = {"S12": 10.0, "S23": 2.0, "S99": 5.0}
    est = lambda i: {"S12": 9.0, "S23": 3.0}           # S99 not sensed
    r = compute_density_report(make("a", truth, est), min_scenarios=1)
    sc = r.scenarios[0]
    assert {s.segment for s in sc.segments} == {"S12", "S23"}
    assert sc.mae == pytest.approx(1.0) and sc.rmse == pytest.approx(1.0)
    assert sc.max_abs_error == pytest.approx(1.0) and r.meets_target


def test_empty_and_bad_args():
    r = compute_density_report([])
    assert not r.meets_target and math.isnan(r.mae)
    with pytest.raises(ValueError):
        compute_density_report([], warmup_frac=1.0)

import pytest

from isacv2x.common.config import load_config
from isacv2x.density.base import feed
from isacv2x.density.synthetic import SyntheticDensity
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.replanner import Replanner

CFG = "config/system.yaml"
cfg = load_config(CFG)


def test_static_covers_all_segments():
    d = SyntheticDensity.static(cfg, {"S12": 7})
    est = d.estimate(123.0)
    assert set(est.counts) == set(cfg.segments)
    assert est.counts["S12"] == 7 and est.counts["S23"] == 0
    assert est.source == "synthetic" and est.timestamp == 123.0


def test_linear_interpolation_and_holds():
    d = SyntheticDensity(cfg, [(10.0, {"S12": 0}), (20.0, {"S12": 10})])
    assert d.truth(0)["S12"] == 0                      # before first keyframe
    assert d.truth(15)["S12"] == pytest.approx(5.0)
    assert d.truth(10)["S12"] == pytest.approx(0.0)
    assert d.truth(20)["S12"] == pytest.approx(10.0)
    assert d.truth(99)["S12"] == 10                    # after last keyframe


def test_t0_offset():
    d = SyntheticDensity(cfg, [(0.0, {"S12": 0}), (10.0, {"S12": 10})], t0=100.0)
    assert d.truth(105.0)["S12"] == pytest.approx(5.0)


def test_validation():
    with pytest.raises(ValueError):
        SyntheticDensity(cfg, [])
    with pytest.raises(ValueError):
        SyntheticDensity(cfg, [(5.0, {}), (5.0, {})])
    with pytest.raises(ValueError):
        SyntheticDensity(cfg, [(0.0, {}), (-1.0, {})])
    with pytest.raises(KeyError):
        SyntheticDensity(cfg, [(0.0, {"S99": 1})])
    with pytest.raises(ValueError):
        SyntheticDensity(cfg, [(0.0, {"S12": -1})])
    with pytest.raises(ValueError):
        SyntheticDensity(cfg, [(0.0, {})], noise_std=-1)


def test_noise_is_seeded_nonnegative_and_truth_stays_clean():
    a = SyntheticDensity.static(cfg, {"S12": 1}, noise_std=2.0, seed=7)
    b = SyntheticDensity.static(cfg, {"S12": 1}, noise_std=2.0, seed=7)
    ea = [a.estimate(t).counts["S12"] for t in range(20)]
    eb = [b.estimate(t).counts["S12"] for t in range(20)]
    assert ea == eb                                    # reproducible
    assert len(set(ea)) > 1                            # varies between calls
    assert min(ea) >= 0.0                              # clipped
    assert a.truth(0)["S12"] == 1.0                    # ground truth is noiseless


def test_smoothing_reduces_variance():
    raw = SyntheticDensity.static(cfg, {"S12": 10}, noise_std=3.0, seed=1)
    sm = SyntheticDensity.static(cfg, {"S12": 10}, noise_std=3.0, seed=1, smooth=True)
    r = [raw.estimate(t).counts["S12"] for t in range(200)]
    s = [sm.estimate(t).counts["S12"] for t in range(200)]
    var = lambda xs: sum((x - sum(xs) / len(xs)) ** 2 for x in xs) / len(xs)
    assert var(s) < var(r)


def test_congestion_event_shape():
    d = SyntheticDensity.congestion_event(cfg, "S12", t_start=10, t_peak=20, t_end=30,
                                          peak=20, base={"S12": 2, "S14": 5})
    assert d.truth(0)["S12"] == 2 and d.truth(0)["S14"] == 5
    assert d.truth(20)["S12"] == pytest.approx(20)
    assert d.truth(15)["S12"] == pytest.approx(11)
    assert d.truth(40)["S12"] == pytest.approx(2) and d.truth(40)["S14"] == 5
    with pytest.raises(ValueError):
        SyntheticDensity.congestion_event(cfg, "S12", t_start=5, t_peak=5, t_end=9, peak=3)


def test_event_drives_reroute_and_recovery_through_replanner():
    d = SyntheticDensity.congestion_event(cfg, "S12", t_start=0, t_peak=10, t_end=20,
                                          peak=20)
    rp = Replanner(RoadGraph(cfg), cfg.routing, "J1", "J2")
    routes = {}
    for t in (0, 10, 20):
        _, res = feed(d, rp, t)
        routes[t] = res.route.segments
    assert routes[0] == ("S12",)                       # clear road: direct
    assert routes[10] != ("S12",)                      # congested: detour via J4-J5
    assert routes[20] == ("S12",)                      # cleared: back to direct

import numpy as np
import pytest

from isacv2x.common.config import load_config
from isacv2x.density.radar_counter import (C, RadarCounter, RadarParams, cfar_2d, detect,
                                           synth_frame)

cfg = load_config("config/system.yaml")
P = RadarParams.from_config(cfg)


def test_params_follow_documented_formulas():
    assert P.range_bin_m == pytest.approx(C / (2 * 20e6))              # c / 2B = 7.5 m
    # v = c f_D / (2 f_c): 8 Hz at 2.4 GHz is ~0.5 m/s
    assert P.velocity_of_row(51, 100) == pytest.approx(8.0 * C / (2 * 2.4e9))
    assert P.row_of_velocity(P.velocity_of_row(63, 100), 100) == 63


def test_detects_single_target_with_range_and_velocity():
    rng = np.random.default_rng(1)
    d = detect(synth_frame(P, [(150.0, 10.0, 20.0)], rng=rng), P)
    assert len(d) == 1
    assert d[0].range_m == pytest.approx(150.0, abs=P.range_bin_m)
    assert d[0].velocity_mps == pytest.approx(10.0, abs=0.6)
    d = detect(synth_frame(P, [(90.0, -7.0, 20.0)], rng=rng), P)
    assert d[0].velocity_mps < 0                                       # receding vs approaching


def test_stationary_target_is_gated_out():
    rng = np.random.default_rng(2)
    assert detect(synth_frame(P, [(100.0, 0.0, 25.0)], rng=rng), P) == []


def test_noise_false_alarm_rate_is_low():
    rng = np.random.default_rng(3)
    fa = sum(len(detect(synth_frame(P, [], rng=rng), P)) for _ in range(300))
    assert fa / 300 < 0.3


def test_cfar_threshold_scales_with_noise_floor():
    rng = np.random.default_rng(4)
    quiet = rng.exponential(1.0, (60, 60))
    loud = quiet * 100
    assert cfar_2d(quiet, (1, 1), (4, 4), 1e-5).sum() == cfar_2d(loud, (1, 1), (4, 4), 1e-5).sum()


def test_bad_input_shape():
    with pytest.raises(ValueError):
        detect(np.zeros(10), P)


def feed(rc, node, n, t, seed):
    rng = np.random.default_rng(seed)
    tg = [(10.0 + 30.0 * i, 6.0 + i, 20.0) for i in range(n)]
    return rc.ingest_frame(node, synth_frame(rc.params, tg, rng=rng), t)


def test_counter_estimate_window_and_unknown_segments():
    rc = RadarCounter(cfg, smoothing_alpha=1.0, window_s=2.0)
    assert rc.estimate(0.0).counts == {}                               # nothing sensed yet
    for k, t in enumerate((0.0, 0.5, 1.0, 1.5)):
        assert feed(rc, "RSU1", 4, t, k) == 4
    est = rc.estimate(1.5)
    assert est.counts == {"S12": 4.0} and est.source == "radar_counter"
    assert est.mean_speed_mps["S12"] > 5
    assert rc.estimate(10.0).counts == {}                              # stale frames dropped
    with pytest.raises(KeyError):
        feed(rc, "NOPE", 1, 0.0, 0)


def test_detections_outside_segment_are_ignored():
    rc = RadarCounter(cfg, smoothing_alpha=1.0)
    rng = np.random.default_rng(5)
    assert rc.ingest_frame("RSU1", synth_frame(rc.params, [(480.0, 9.0, 20.0)],
                                               rng=rng), 0.0) == 0     # S12 is 400 m


def test_start_requires_decoder():
    with pytest.raises(NotImplementedError, match="PARM"):
        RadarCounter(cfg).start()

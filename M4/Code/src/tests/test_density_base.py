import pytest

from isacv2x.common.config import load_config
from isacv2x.common.logging_utils import EventLogger
from isacv2x.density.base import (DensityEstimate, DensityEstimator, EmaSmoother,
                                  covered_segments, feed)
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.replanner import Replanner

CFG = "config/system.yaml"


class Fixed(DensityEstimator):
    name = "fixed"

    def __init__(self, counts):
        self.counts, self.started, self.stopped = counts, 0, 0

    def estimate(self, now):
        return DensityEstimate(dict(self.counts), now, self.name)

    def start(self):
        self.started += 1

    def stop(self):
        self.stopped += 1


def test_abstract_cannot_be_instantiated():
    with pytest.raises(TypeError):
        DensityEstimator()


def test_context_manager_calls_start_stop():
    e = Fixed({})
    with e as same:
        assert same is e and e.started == 1 and e.stopped == 0
    assert e.stopped == 1


def test_ema_first_value_initialises_then_blends():
    s = EmaSmoother(0.5)
    assert s.update({"S12": 10}) == {"S12": 10.0}
    assert s.update({"S12": 20}) == {"S12": 15.0}
    assert s.update({"S12": 20, "S23": 4}) == {"S12": 17.5, "S23": 4.0}
    s.reset()
    assert s.update({"S12": 8}) == {"S12": 8.0}


def test_ema_alpha_one_is_passthrough_and_validation():
    s = EmaSmoother(1.0)
    s.update({"S12": 3})
    assert s.update({"S12": 9}) == {"S12": 9.0}
    for bad in (0.0, -0.1, 1.5):
        with pytest.raises(ValueError):
            EmaSmoother(bad)


def test_covered_segments_follow_node_placement():
    cfg = load_config(CFG)
    assert covered_segments(cfg) == ("S12", "S23", "S45", "S25")   # S56,S14,S36 unsensed


def test_feed_drives_replanner_and_logs():
    cfg = load_config(CFG)
    rp = Replanner(RoadGraph(cfg), cfg.routing, "J1", "J3")
    log = EventLogger()
    est, res = feed(Fixed({"S12": 20, "S23": 20}), rp, now=3.0, logger=log)
    assert est.timestamp == 3.0 and res.replanned
    assert res.route.segments != ("S12", "S23")
    ev = log.events("density")[0]
    assert ev["source"] == "fixed" and ev["counts"] == {"S12": 20, "S23": 20}

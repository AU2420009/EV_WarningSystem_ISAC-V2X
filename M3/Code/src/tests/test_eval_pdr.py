import math
import tempfile
from pathlib import Path

import pytest
import yaml

from isacv2x.alert.receiver import AlertReceiver
from isacv2x.alert.relay import Relay
from isacv2x.alert.transmitter import AlertTransmitter
from isacv2x.backends.loopback import LoopbackChannel
from isacv2x.common.config import load_config, parse_config
from isacv2x.common.logging_utils import EventLogger
from isacv2x.eval.pdr import compute_pdr, pdr_from_log, wilson_interval


def tx(seq, route=("S12", "S23"), ev=1, copy=1):
    return {"event": "alert_tx", "ev_id": ev, "seq": seq, "route": list(route), "copy": copy}


def rx(node, seq, ev=1, reason="ok", on_route=True):
    return {"event": "alert_rx", "node": node, "ev_id": ev, "seq": seq,
            "reason": reason, "on_route": on_route}


NODES = {"A": "S23", "B": "S23", "C": "S23"}


def test_wilson_interval_known_values():
    lo, hi = wilson_interval(9, 10)
    assert lo == pytest.approx(0.5958, abs=1e-3) and hi == pytest.approx(0.9821, abs=1e-3)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(100, 100)
    assert hi == pytest.approx(1.0) and lo > 0.96


def test_perfect_delivery_three_nodes():
    ev = [tx(s) for s in range(1, 101)] + [rx(n, s) for n in NODES for s in range(1, 101)]
    r = compute_pdr(ev, NODES)
    assert r.sent == 100 and r.overall_pdr == 1.0
    assert r.meets_target and r.meets_target_conservative
    assert [n.delivered for n in r.nodes] == [100, 100, 100]


def test_repeat_copies_count_once():
    ev = [tx(1, copy=c) for c in (1, 2, 3)] + [rx("A", 1)] * 3 + [rx("B", 1), rx("C", 1)]
    r = compute_pdr(ev, NODES)
    assert r.sent == 1 and r.overall_pdr == 1.0


def test_one_weak_node_fails_the_criterion():
    ev = [tx(s) for s in range(1, 101)]
    ev += [rx("A", s) for s in range(1, 101)] + [rx("B", s) for s in range(1, 101)]
    ev += [rx("C", s) for s in range(1, 81)]                         # C gets 80 %
    r = compute_pdr(ev, NODES)
    c = next(n for n in r.nodes if n.node == "C")
    assert c.pdr == pytest.approx(0.8) and not c.meets_target
    assert not r.meets_target and r.overall_pdr == pytest.approx(280 / 300)


def test_too_few_nodes_fails_even_with_perfect_pdr():
    ev = [tx(1)] + [rx("A", 1), rx("B", 1)]
    r = compute_pdr(ev, {"A": "S23", "B": "S23"})
    assert r.overall_pdr == 1.0 and not r.meets_target


def test_small_sample_passes_point_estimate_but_not_conservative():
    ev = [tx(s) for s in range(1, 11)]
    ev += [rx(n, s) for n in NODES for s in range(1, 11)]
    r = compute_pdr(ev, NODES)
    assert r.meets_target and not r.meets_target_conservative       # 10/10, CI low ~0.72


def test_off_route_nodes_are_not_expected():
    ev = [tx(1, route=("S12", "S23")), rx("A", 1)]
    r = compute_pdr(ev, {"A": "S23", "B": "S56"}, min_nodes=1)
    b = next(n for n in r.nodes if n.node == "B")
    assert b.expected == 0 and math.isnan(b.pdr) and not b.meets_target
    assert r.meets_target                                           # B is ignored


def test_none_segment_expects_everything_and_wrong_deliveries_ignored():
    ev = [tx(1, route=("S99",)), rx("A", 1, on_route=False)]
    r = compute_pdr(ev, {"A": None}, min_nodes=1)
    assert r.nodes[0].expected == 1 and r.nodes[0].delivered == 0   # off-route rx doesn't count


def test_unexpected_and_non_ok_rx():
    ev = [tx(1), rx("A", 99), rx("A", 1, reason="corrupt"), rx("A", 1, ev=2)]
    r = compute_pdr(ev, {"A": "S23"}, min_nodes=1)
    assert r.unexpected_rx == 2 and r.nodes[0].delivered == 0       # wrong ev_id is unexpected


def test_empty_log():
    r = compute_pdr([], NODES)
    assert r.sent == 0 and math.isnan(r.overall_pdr) and not r.meets_target


def test_matches_ground_truth_on_loopback_chain_via_log_file():
    raw = yaml.safe_load(open("config/system.yaml"))
    raw["alert"]["repeat"] = 3
    cfg = parse_config(raw)
    path = Path(tempfile.mkdtemp()) / "run.jsonl"
    with EventLogger(path) as log:
        ev_link = LoopbackChannel(loss=0.1, seed=11)
        hop = [LoopbackChannel(loss=0.1, seed=12 + i) for i in range(3)]
        t = AlertTransmitter(cfg, ev_link.sender(), logger=log, sleep=lambda s: None)
        relay = Relay(cfg, "RLY1", ev_link.receiver(), [h.sender() for h in hop])
        rxs = [AlertReceiver(cfg, h.receiver(), f"RX{i + 1}", segment="S23", logger=log)
               for i, h in enumerate(hop)]
        truth = [set() for _ in rxs]
        for _ in range(300):
            s = t.send_alert(("S12", "S23"))
            relay.drain()
            for i, r in enumerate(rxs):
                for o in r.drain():
                    if o.reason == "ok":
                        truth[i].add(o.event.alert.seq)
    rep = pdr_from_log(path, cfg, {f"RX{i + 1}": "S23" for i in range(3)})
    assert rep.sent == 300
    assert [n.delivered for n in rep.nodes] == [len(g) for g in truth]
    assert rep.target == cfg.eval.pdr_target and rep.min_nodes == 3
    assert rep.meets_target and rep.meets_target_conservative       # ~0.99 with 300 alerts

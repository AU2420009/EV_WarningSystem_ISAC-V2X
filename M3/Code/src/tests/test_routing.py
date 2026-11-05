import copy
import random

import pytest
import yaml

from isacv2x.common.config import load_config, parse_config
from isacv2x.routing.astar import astar, plan
from isacv2x.routing.dijkstra import dijkstra
from isacv2x.routing.graph import NoRoute, RoadGraph

CFG = "config/system.yaml"


def make_graph(mutate=None):
    if mutate is None:
        return RoadGraph(load_config(CFG))
    raw = yaml.safe_load(open(CFG))
    mutate(raw)
    return RoadGraph(parse_config(raw))


def test_empty_network_takes_shortest_geometric_path():
    g = make_graph()
    for algo in (dijkstra, astar):
        r = algo(g, "J1", "J6")
        assert r.cost == pytest.approx(1100.0)       # 400+400+300 or similar, all equal
        assert r.junctions[0] == "J1" and r.junctions[-1] == "J6"
        assert len(r.segments) == len(r.junctions) - 1


def test_same_junction_is_zero_cost():
    g = make_graph()
    r = dijkstra(g, "J2", "J2")
    assert r.cost == 0.0 and r.segments == () and r.junctions == ("J2",)


def test_congestion_causes_reroute():
    g = make_graph()
    base = astar(g, "J1", "J3")
    assert base.segments == ("S12", "S23")
    g.set_density_counts({"S12": 20, "S23": 20})     # both fully congested
    detour = astar(g, "J1", "J3")
    assert detour.segments != base.segments
    assert detour.segments == ("S14", "S45", "S56", "S36") or "S25" in detour.segments


def test_bidirectional_shares_density():
    g = make_graph()
    g.set_density_counts({"S12": 10})
    fwd = astar(g, "J1", "J2").cost
    back = astar(g, "J2", "J1").cost
    assert fwd == pytest.approx(back)
    assert fwd == pytest.approx(400 * (1 + 3.0 * 0.5 ** 2.0))


def test_density_clamped_and_validated():
    g = make_graph()
    g.set_density_counts({"S12": 999})
    assert g.density_norm("S12") == 1.0
    with pytest.raises(KeyError):
        g.set_density_norm({"S99": 0.5})
    with pytest.raises(ValueError):
        g.set_density_norm({"S12": float("nan")})


def test_directed_graph_when_not_bidirectional():
    g = make_graph(lambda r: r.__setitem__("bidirectional", False))
    assert dijkstra(g, "J1", "J2").segments == ("S12",)
    with pytest.raises(NoRoute):
        dijkstra(g, "J2", "J1")


def test_disconnected_and_unknown_junction():
    g = make_graph(lambda r: r["junctions"].__setitem__("J7", {"x": 5000, "y": 5000}))
    with pytest.raises(NoRoute):
        astar(g, "J1", "J7")
    with pytest.raises(NoRoute):
        dijkstra(g, "J1", "J7")
    with pytest.raises(KeyError):
        astar(g, "J1", "NOPE")


def test_inadmissible_segment_length_rejected():
    def shorten(r):
        r["segments"][0]["length_m"] = 100     # J1->J2 is 400 m apart
    with pytest.raises(ValueError):
        make_graph(shorten)


def test_dijkstra_and_astar_agree_on_random_density():
    g = make_graph()
    rng = random.Random(42)
    nodes = g.junctions
    astar_exp = dijk_exp = 0
    for _ in range(300):
        g.set_density_norm({s: rng.random() for s in g.segments})
        a, b = rng.sample(nodes, 2)
        rd, ra = dijkstra(g, a, b), astar(g, a, b)
        assert ra.cost == pytest.approx(rd.cost)
        # reported cost must equal re-pricing the returned path
        assert g.path_cost(ra.segments, a) == pytest.approx(ra.cost)
        assert g.path_cost(rd.segments, a) == pytest.approx(rd.cost)
        astar_exp += ra.expanded
        dijk_exp += rd.expanded
    assert astar_exp <= dijk_exp


def test_plan_dispatch():
    g = make_graph()
    assert plan(g, "J1", "J6", "astar").cost == plan(g, "J1", "J6", "dijkstra").cost
    with pytest.raises(ValueError):
        plan(g, "J1", "J6", "bfs")

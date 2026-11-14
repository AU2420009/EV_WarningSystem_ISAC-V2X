"""Route recomputation time and route adaptation.

Criterion (report, Table II): the route is recomputed within a reasonable window
(``routing.replan_deadline_s``) when congestion changes, and the route actually adapts.

Two views:

* ``compute_replan_report``  from ``replan`` log events (what happened in a run).
* ``benchmark_planners``     Dijkstra vs A* on random density maps for the same graph, to
                             show both are far inside the deadline and agree on cost.
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import Iterable, Mapping

from isacv2x.common.config import SystemConfig
from isacv2x.eval.stats import percentile
from isacv2x.routing.astar import astar
from isacv2x.routing.dijkstra import dijkstra
from isacv2x.routing.graph import RoadGraph


@dataclass(frozen=True)
class ReplanReport:
    replans: int                  # searches actually run (initial + density-triggered)
    route_changes: int            # searches that produced a different route (after the first)
    skipped: int                  # updates below the change threshold (no search)
    mean_s: float
    p95_s: float
    max_s: float
    deadline_s: float
    deadline_misses: int
    meets_target: bool            # >= 1 search, no misses, and the route adapted at least once


def compute_replan_report(events: Iterable[Mapping], deadline_s: float) -> ReplanReport:
    ev = [e for e in events if e.get("event") == "replan"]
    runs = [e for e in ev if e.get("replanned")]
    times = [e["compute_s"] for e in runs]
    misses = sum(t > deadline_s for t in times)
    # the very first plan is "initial", not an adaptation
    changes = sum(1 for e in runs if e.get("route_changed") and e.get("reason") != "initial")
    return ReplanReport(
        len(runs), changes, len(ev) - len(runs),
        sum(times) / len(times) if times else math.nan,
        percentile(times, 95), max(times) if times else math.nan,
        deadline_s, misses, bool(runs) and misses == 0 and changes >= 1)


@dataclass(frozen=True)
class PlannerBench:
    algorithm: str
    trials: int
    mean_s: float
    p95_s: float
    max_s: float
    mean_expanded: float


def benchmark_planners(cfg: SystemConfig, *, trials: int = 200, seed: int | None = None
                       ) -> tuple[dict[str, PlannerBench], int]:
    """Time both planners on random density maps over random junction pairs.

    Returns (per-algorithm stats, number of trials where the two disagreed on path cost).
    """
    rng = random.Random(cfg.seed if seed is None else seed)
    graph = RoadGraph(cfg)
    juncs = graph.junctions
    t_d, t_a, ex_d, ex_a, mismatch = [], [], [], [], 0
    for _ in range(trials):
        graph.set_density_norm({s: rng.random() for s in graph.segments})
        src, dst = rng.sample(juncs, 2)
        t0 = time.perf_counter(); rd = dijkstra(graph, src, dst)
        t1 = time.perf_counter(); ra = astar(graph, src, dst)
        t2 = time.perf_counter()
        t_d.append(t1 - t0); t_a.append(t2 - t1)
        ex_d.append(rd.expanded); ex_a.append(ra.expanded)
        mismatch += abs(rd.cost - ra.cost) > 1e-6

    def bench(name: str, t: list[float], ex: list[int]) -> PlannerBench:
        return PlannerBench(name, trials, sum(t) / trials, percentile(t, 95), max(t),
                            sum(ex) / trials)

    return {"dijkstra": bench("dijkstra", t_d, ex_d),
            "astar": bench("astar", t_a, ex_a)}, mismatch

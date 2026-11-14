"""Re-plan the EV route when live density changes.

The Replanner owns one (src, dst) trip. Each density update is compared with the density
snapshot used for the *current* route; the route is recomputed only if some segment's
normalised density moved by at least ``routing.replan_min_change`` (or ``force=True``).
Every recompute is timed and checked against ``routing.replan_deadline_s`` -- the
"route adaptation" criterion in the Milestone 1 report.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Mapping

from isacv2x.common.config import RoutingCfg
from isacv2x.common.logging_utils import EventLogger
from isacv2x.routing.astar import plan
from isacv2x.routing.graph import NoRoute, RoadGraph, Route


@dataclass(frozen=True)
class ReplanResult:
    route: Route | None             # None only if no path exists
    replanned: bool                 # was the search actually re-run?
    route_changed: bool             # do the segments differ from the previous route?
    reason: str                     # initial | density_change | forced | below_threshold | no_route
    max_change: float               # largest normalised-density change vs. last plan
    compute_s: float                # search time (0.0 if not replanned)
    deadline_met: bool              # compute_s <= replan_deadline_s


class Replanner:
    def __init__(self, graph: RoadGraph, cfg: RoutingCfg, src: str, dst: str, *,
                 logger: EventLogger | None = None,
                 clock: Callable[[], float] = time.perf_counter):
        graph.check_endpoints(src, dst)
        self.graph, self.cfg = graph, cfg
        self.src, self.dst = src, dst
        self._log = logger
        self._clock = clock
        self.route: Route | None = None
        self._planned_density: dict[str, float] | None = None
        self.history: list[ReplanResult] = []

    # ------------------------------------------------------------ public API
    def update_counts(self, counts: Mapping[str, float], *, force: bool = False) -> ReplanResult:
        """Feed vehicle counts per segment (normalised by segment capacity)."""
        self.graph.set_density_counts(counts)
        return self._maybe_replan(force)

    def update_norm(self, norm: Mapping[str, float], *, force: bool = False) -> ReplanResult:
        """Feed already-normalised (0..1) density per segment."""
        self.graph.set_density_norm(norm)
        return self._maybe_replan(force)

    def set_trip(self, src: str, dst: str) -> ReplanResult:
        """Change the trip (e.g. EV reached a junction) and plan immediately."""
        self.graph.check_endpoints(src, dst)
        self.src, self.dst = src, dst
        self._planned_density = None
        return self._maybe_replan(force=True)

    def stats(self) -> dict[str, float]:
        runs = [r for r in self.history if r.replanned]
        if not runs:
            return {"replans": 0, "changed": 0, "max_compute_s": 0.0,
                    "mean_compute_s": 0.0, "deadline_miss": 0}
        times = [r.compute_s for r in runs]
        return {
            "replans": len(runs),
            "changed": sum(r.route_changed for r in runs),
            "max_compute_s": max(times),
            "mean_compute_s": sum(times) / len(times),
            "deadline_miss": sum(not r.deadline_met for r in runs),
        }

    # -------------------------------------------------------------- internals
    def _max_change(self) -> float:
        if self._planned_density is None:
            return float("inf")
        now = self.graph.densities()
        return max((abs(now[s] - self._planned_density[s]) for s in now), default=0.0)

    def _maybe_replan(self, force: bool) -> ReplanResult:
        change = self._max_change()
        first = self._planned_density is None
        if not (first or force or change >= self.cfg.replan_min_change):
            return self._record(ReplanResult(
                self.route, False, False, "below_threshold", change, 0.0, True))

        reason = "initial" if first else ("forced" if force else "density_change")
        prev_segments = self.route.segments if self.route else None
        t0 = self._clock()
        try:
            new_route: Route | None = plan(self.graph, self.src, self.dst,
                                           self.cfg.algorithm)
        except NoRoute:
            new_route, reason = None, "no_route"
        compute_s = self._clock() - t0

        self._planned_density = self.graph.densities()
        self.route = new_route
        changed = (new_route.segments if new_route else None) != prev_segments
        return self._record(ReplanResult(
            new_route, True, changed, reason,
            0.0 if change == float("inf") else change, compute_s,
            compute_s <= self.cfg.replan_deadline_s))

    def _record(self, res: ReplanResult) -> ReplanResult:
        self.history.append(res)
        if self._log is not None:
            self._log.log(
                "replan", replanned=res.replanned, route_changed=res.route_changed,
                reason=res.reason, max_change=res.max_change, compute_s=res.compute_s,
                deadline_met=res.deadline_met, algorithm=self.cfg.algorithm,
                route=res.route.segments if res.route else None,
                cost=res.route.cost if res.route else None,
                expanded=res.route.expanded if res.route else None)
        return res

"""Road graph with density-dependent edge costs.

Junctions are vertices; each configured segment becomes one directed edge (two if
``bidirectional``). Both directions of a segment share one density value, because the
density proxy is measured per segment, not per direction.

    cost(edge) = length_m * (1 + congestion_weight * density_norm ** congestion_exponent)

``density_norm`` is vehicle count / segment capacity, clamped to [0, 1].
Since cost >= length >= straight-line distance, the Euclidean distance between junctions
is an admissible A* heuristic. Construction rejects segments shorter than the straight
line between their endpoints, which would break that guarantee.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterator, Mapping

from isacv2x.common.config import RoutingCfg, SystemConfig


class NoRoute(Exception):
    """No path exists between the requested junctions."""


@dataclass(frozen=True)
class Edge:
    segment: str
    src: str
    dst: str
    length_m: float


@dataclass(frozen=True)
class Route:
    junctions: tuple[str, ...]      # visited junctions, start..goal
    segments: tuple[str, ...]       # segment ids traversed, in order (alert route)
    cost: float
    expanded: int = 0               # nodes popped from the queue (search effort)

    @property
    def length_hops(self) -> int:
        return len(self.segments)


class RoadGraph:
    def __init__(self, cfg: SystemConfig):
        self._coords = {j.id: (j.x, j.y) for j in cfg.junctions.values()}
        self._capacity = {s.id: s.capacity_veh for s in cfg.segments.values()}
        self._routing: RoutingCfg = cfg.routing
        self._adj: dict[str, list[Edge]] = {j: [] for j in cfg.junctions}
        self._density: dict[str, float] = {sid: 0.0 for sid in cfg.segments}

        for s in cfg.segments.values():
            straight = self.heuristic(s.src, s.dst)
            if s.length_m + 1e-9 < straight:
                raise ValueError(
                    f"segment {s.id}: length {s.length_m} m is shorter than the "
                    f"straight-line distance {straight:.1f} m between its junctions "
                    f"(would make the A* heuristic inadmissible)")
            self._adj[s.src].append(Edge(s.id, s.src, s.dst, s.length_m))
            if cfg.bidirectional:
                self._adj[s.dst].append(Edge(s.id, s.dst, s.src, s.length_m))

    # ------------------------------------------------------------- structure
    @property
    def junctions(self) -> tuple[str, ...]:
        return tuple(self._adj)

    @property
    def segments(self) -> tuple[str, ...]:
        return tuple(self._density)

    def has_junction(self, j: str) -> bool:
        return j in self._adj

    def edges_from(self, junction: str) -> Iterator[Edge]:
        return iter(self._adj[junction])

    def heuristic(self, a: str, b: str) -> float:
        (x1, y1), (x2, y2) = self._coords[a], self._coords[b]
        return math.hypot(x1 - x2, y1 - y2)

    # --------------------------------------------------------------- density
    def set_density_norm(self, norm: Mapping[str, float]) -> None:
        """Set normalised density (0..1) for the given segments; others are unchanged."""
        for sid, v in norm.items():
            if sid not in self._density:
                raise KeyError(f"unknown segment '{sid}'")
            if math.isnan(v):
                raise ValueError(f"density for {sid} is NaN")
            self._density[sid] = min(max(float(v), 0.0), 1.0)

    def set_density_counts(self, counts: Mapping[str, float]) -> None:
        """Set density from vehicle counts, normalised by each segment's capacity."""
        self.set_density_norm({
            sid: c / self._capacity[sid] if sid in self._capacity else 0.0
            for sid, c in counts.items()
        })

    def density_norm(self, segment: str) -> float:
        return self._density[segment]

    def densities(self) -> dict[str, float]:
        return dict(self._density)

    def clear_density(self) -> None:
        for sid in self._density:
            self._density[sid] = 0.0

    # ------------------------------------------------------------------ cost
    def cost(self, edge: Edge) -> float:
        d = self._density[edge.segment]
        r = self._routing
        return edge.length_m * (1.0 + r.congestion_weight * d ** r.congestion_exponent)

    def path_cost(self, segments: tuple[str, ...], start: str) -> float:
        """Re-price a segment sequence under the *current* density (used by tests/replanner)."""
        at, total = start, 0.0
        for sid in segments:
            edge = next((e for e in self._adj[at] if e.segment == sid), None)
            if edge is None:
                raise ValueError(f"segment {sid} does not leave junction {at}")
            total += self.cost(edge)
            at = edge.dst
        return total

    def check_endpoints(self, src: str, dst: str) -> None:
        for j in (src, dst):
            if j not in self._adj:
                raise KeyError(f"unknown junction '{j}'")

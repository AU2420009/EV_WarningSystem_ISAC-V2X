"""Dijkstra shortest path on the density-weighted RoadGraph."""
from __future__ import annotations

import heapq
import itertools

from isacv2x.routing.graph import Edge, NoRoute, RoadGraph, Route


def dijkstra(graph: RoadGraph, src: str, dst: str) -> Route:
    graph.check_endpoints(src, dst)
    tie = itertools.count()                       # deterministic tie-breaking
    best: dict[str, float] = {src: 0.0}
    prev: dict[str, Edge] = {}
    heap: list[tuple[float, int, str]] = [(0.0, next(tie), src)]
    done: set[str] = set()
    expanded = 0

    while heap:
        g, _, u = heapq.heappop(heap)
        if u in done:
            continue
        done.add(u)
        expanded += 1
        if u == dst:
            return _build(prev, src, dst, g, expanded)
        for e in graph.edges_from(u):
            ng = g + graph.cost(e)
            if ng < best.get(e.dst, float("inf")):
                best[e.dst] = ng
                prev[e.dst] = e
                heapq.heappush(heap, (ng, next(tie), e.dst))
    raise NoRoute(f"no path from {src} to {dst}")


def _build(prev: dict[str, Edge], src: str, dst: str, cost: float, expanded: int) -> Route:
    junctions, segments = [dst], []
    at = dst
    while at != src:
        e = prev[at]
        segments.append(e.segment)
        junctions.append(e.src)
        at = e.src
    junctions.reverse()
    segments.reverse()
    return Route(tuple(junctions), tuple(segments), cost, expanded)

"""A* shortest path on the density-weighted RoadGraph.

Heuristic: straight-line distance between junctions. It never overestimates because every
edge costs at least its length, and each length is at least the straight-line distance
(enforced in RoadGraph.__init__). Hence A* returns the same optimal cost as Dijkstra.
"""
from __future__ import annotations

import heapq
import itertools

from isacv2x.routing.dijkstra import _build
from isacv2x.routing.graph import Edge, NoRoute, RoadGraph, Route


def astar(graph: RoadGraph, src: str, dst: str) -> Route:
    graph.check_endpoints(src, dst)
    tie = itertools.count()
    g_best: dict[str, float] = {src: 0.0}
    prev: dict[str, Edge] = {}
    heap: list[tuple[float, int, float, str]] = [
        (graph.heuristic(src, dst), next(tie), 0.0, src)]
    closed: set[str] = set()
    expanded = 0

    while heap:
        _, _, g, u = heapq.heappop(heap)
        if u in closed:
            continue
        closed.add(u)
        expanded += 1
        if u == dst:
            return _build(prev, src, dst, g, expanded)
        for e in graph.edges_from(u):
            ng = g + graph.cost(e)
            if ng < g_best.get(e.dst, float("inf")):
                g_best[e.dst] = ng
                prev[e.dst] = e
                heapq.heappush(heap, (ng + graph.heuristic(e.dst, dst), next(tie), ng, e.dst))
    raise NoRoute(f"no path from {src} to {dst}")


def plan(graph: RoadGraph, src: str, dst: str, algorithm: str) -> Route:
    """Dispatch on routing.algorithm from system.yaml."""
    if algorithm == "astar":
        return astar(graph, src, dst)
    if algorithm == "dijkstra":
        from isacv2x.routing.dijkstra import dijkstra
        return dijkstra(graph, src, dst)
    raise ValueError(f"unknown routing algorithm '{algorithm}'")

"""Alert latency: time from the EV building the alert to a node decoding it.

Criterion (report, Table II): alert latency within a limit. The report gives no number,
so the limit is ``eval.alert_latency_p95_s`` (placeholder, see config) applied to the 95th
percentile.

Latency comes from ``alert_rx`` events (reason ``ok``, i.e. the first copy that got
through at each node): ``latency_s`` = receive wall-clock minus the alert's own timestamp.
That is only meaningful when transmitter and receivers share a clock (same host, or
NTP/PTP-synchronised hosts). ``negative`` counts samples below zero, a sign of clock skew;
if any exist the result is not trusted and ``meets_target`` is False.

On the loopback demo this measures software path time only, not radio time.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping

from isacv2x.common.config import SystemConfig
from isacv2x.eval.stats import percentile


@dataclass(frozen=True)
class LatencyReport:
    samples: int
    mean_s: float
    p50_s: float
    p95_s: float
    max_s: float
    negative: int                 # clock-skew indicator
    limit_s: float
    meets_target: bool


def compute_latency(events: Iterable[Mapping], limit_s: float = 0.100) -> LatencyReport:
    lat = [e["latency_s"] for e in events
           if e.get("event") == "alert_rx" and e.get("reason") == "ok"
           and e.get("on_route") and e.get("latency_s") is not None]
    neg = sum(x < 0 for x in lat)
    p95 = percentile(lat, 95)
    return LatencyReport(
        len(lat), sum(lat) / len(lat) if lat else math.nan, percentile(lat, 50), p95,
        max(lat) if lat else math.nan, neg, limit_s,
        bool(lat) and neg == 0 and p95 <= limit_s)


# --------------------------------------------------------------- time saved
@dataclass(frozen=True)
class TimeSaved:
    """Density-aware route vs the static shortest route, priced under the sensed density.

    Model-based, not measured: each route is priced with the graph's congestion cost
    (length * (1 + w * density**e)) at every alert, then divided by ``speed_mps``.
    """
    decisions: int
    baseline_s: float             # mean modelled trip time of the static (zero-density) route
    adaptive_s: float             # mean modelled trip time of the route actually used
    saved_s: float
    saved_pct: float
    worse_decisions: int          # alerts where the adaptive route was costlier
    meets_target: bool            # >= 1 decision and mean saving >= 0


def compute_time_saved(events: Iterable[Mapping], cfg: SystemConfig, src: str, dst: str,
                       speed_mps: float = 10.0) -> TimeSaved:
    from isacv2x.routing.dijkstra import dijkstra
    from isacv2x.routing.graph import RoadGraph
    if speed_mps <= 0:
        raise ValueError("speed_mps must be > 0")
    graph = RoadGraph(cfg)
    baseline = dijkstra(graph, src, dst).segments            # density is all zero here
    density, route = None, None
    base_t, adap_t, worse = [], [], 0
    for e in events:
        k = e.get("event")
        if k == "density" and e.get("counts") is not None:
            density = e["counts"]
        elif k == "replan" and e.get("replanned") and e.get("route") is not None:
            route = tuple(e["route"])
        elif k == "alert_tx" and e.get("copy") == 1 and density is not None and route:
            graph.clear_density()
            graph.set_density_counts(density)
            b, a = graph.path_cost(baseline, src), graph.path_cost(route, src)
            base_t.append(b / speed_mps)
            adap_t.append(a / speed_mps)
            worse += a > b + 1e-9
    n = len(base_t)
    mb = sum(base_t) / n if n else math.nan
    ma = sum(adap_t) / n if n else math.nan
    return TimeSaved(n, mb, ma, mb - ma if n else math.nan,
                     100.0 * (mb - ma) / mb if n and mb else math.nan, worse,
                     n > 0 and ma <= mb + 1e-9)

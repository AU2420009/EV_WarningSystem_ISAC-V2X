"""Packet delivery ratio (PDR) of the alert broadcast, from the event logs.

Criterion (report, Table II): PDR >= 90 % across 3 or more receiving nodes.

Definitions
-----------
* An *alert* is one (ev_id, seq) seen in an ``alert_tx`` event. Repeat copies share the
  same key, so PDR counts alerts, not copies: an alert is delivered to a node if at least
  one copy was decoded there (``alert_rx`` with reason ``ok`` and ``on_route``).
* A node is *expected* to receive an alert if its segment is on the alert's route, or if
  the node has no segment (mapping value ``None``).
* ``meets_target`` is the point-estimate test: >= ``min_nodes`` expected nodes and every
  such node at or above ``target``. ``meets_target_conservative`` additionally requires
  the lower end of the 95 % Wilson interval to reach the target, which is what a small
  number of alerts can honestly support.

Use one log file per run: (ev_id, seq) restarts when the transmitter restarts, and two
runs in one log would be mixed together.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping

from isacv2x.common.config import SystemConfig
from isacv2x.common.logging_utils import read_jsonl


def wilson_interval(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95 % Wilson score interval for a proportion k/n. (0, 1) when n == 0."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass(frozen=True)
class NodePDR:
    node: str
    expected: int
    delivered: int
    pdr: float                  # NaN if nothing was expected
    ci_low: float
    ci_high: float
    meets_target: bool          # pdr >= target (and expected > 0)
    meets_target_ci: bool       # ci_low >= target


@dataclass(frozen=True)
class PDRReport:
    sent: int                   # distinct alerts transmitted
    nodes: tuple[NodePDR, ...]
    overall_pdr: float          # pooled over nodes with expected > 0
    target: float
    min_nodes: int
    meets_target: bool
    meets_target_conservative: bool
    unexpected_rx: int          # deliveries that match no transmitted alert

    def rows(self) -> list[dict]:
        return [asdict(n) for n in self.nodes]


def compute_pdr(events: Iterable[Mapping], node_segments: Mapping[str, str | None], *,
                target: float = 0.90, min_nodes: int = 3) -> PDRReport:
    events = list(events)
    routes: dict[tuple, tuple[str, ...]] = {}
    for e in events:
        if e.get("event") == "alert_tx":
            routes[(e["ev_id"], e["seq"])] = tuple(e["route"])

    delivered: dict[str, set[tuple]] = {n: set() for n in node_segments}
    unexpected = 0
    for e in events:
        if e.get("event") != "alert_rx" or e.get("reason") != "ok":
            continue
        node = e.get("node")
        if node not in delivered or not e.get("on_route"):
            continue
        key = (e.get("ev_id"), e.get("seq"))
        if key in routes:
            delivered[node].add(key)
        else:
            unexpected += 1

    nodes = []
    for node, seg in node_segments.items():
        expected = {k for k, r in routes.items() if seg is None or seg in r}
        got = delivered[node] & expected
        n, k = len(expected), len(got)
        lo, hi = wilson_interval(k, n)
        pdr = k / n if n else math.nan
        nodes.append(NodePDR(node, n, k, pdr, lo, hi,
                             n > 0 and pdr >= target, n > 0 and lo >= target))

    active = [x for x in nodes if x.expected > 0]
    tot_e, tot_d = sum(x.expected for x in active), sum(x.delivered for x in active)
    enough = len(active) >= min_nodes
    return PDRReport(
        sent=len(routes), nodes=tuple(nodes),
        overall_pdr=tot_d / tot_e if tot_e else math.nan,
        target=target, min_nodes=min_nodes,
        meets_target=enough and all(x.meets_target for x in active),
        meets_target_conservative=enough and all(x.meets_target_ci for x in active),
        unexpected_rx=unexpected,
    )


def pdr_from_log(path: str | Path, cfg: SystemConfig,
                 node_segments: Mapping[str, str | None]) -> PDRReport:
    """Compute PDR from a JSONL log using the targets in system.yaml (eval section)."""
    events = [e for e in read_jsonl(path) if e["event"] in ("alert_tx", "alert_rx")]
    return compute_pdr(events, node_segments, target=cfg.eval.pdr_target,
                       min_nodes=cfg.eval.min_receiving_nodes)

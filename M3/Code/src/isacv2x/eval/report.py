"""Milestone 1 Table II checklist from one run's log plus the signal-ID corpus.

Each criterion is a ``Criterion`` with passed True/False, or None when it can't be judged
automatically (implementation feasibility).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from isacv2x.common.config import SystemConfig
from isacv2x.eval.density_validation import compute_density_report
from isacv2x.eval.latency import compute_latency, compute_time_saved
from isacv2x.eval.pdr import compute_pdr
from isacv2x.eval.replan import compute_replan_report
from isacv2x.eval.signal_id import SignalIdResult


@dataclass(frozen=True)
class Criterion:
    name: str
    passed: bool | None
    value: str
    target: str
    note: str = ""


def check_end_to_end(events: list[Mapping], min_nodes: int) -> tuple[bool, str]:
    """density -> replan -> alert chain: every alert carries the route planned at that time,
    the route adapted at least once, and >= min_nodes distinct nodes decoded alerts."""
    current, adapted, n_tx, bad, seen_density = None, 0, 0, 0, False
    for e in events:
        k = e.get("event")
        if k == "density":
            seen_density = True
        elif k == "replan" and e.get("replanned") and e.get("route") is not None:
            if current is not None and tuple(e["route"]) != current:
                adapted += 1
            current = tuple(e["route"])
        elif k == "alert_tx" and e.get("copy") == 1:
            n_tx += 1
            bad += tuple(e["route"]) != current
    nodes = {e["node"] for e in events if e.get("event") == "alert_rx"
             and e.get("reason") == "ok"}
    ok = seen_density and n_tx > 0 and bad == 0 and adapted >= 1 and len(nodes) >= min_nodes
    return ok, (f"{n_tx} alerts, {adapted} route adaptations, {bad} stale-route alerts, "
                f"{len(nodes)} receiving nodes")


def build_report(events: Iterable[Mapping], cfg: SystemConfig,
                 node_segments: Mapping[str, str | None],
                 signal_id: Mapping[str, SignalIdResult] | None = None,
                 trip: tuple[str, str] | None = None) -> list[Criterion]:
    ev = list(events)
    e = cfg.eval
    pdr = compute_pdr(ev, node_segments, target=e.pdr_target, min_nodes=e.min_receiving_nodes)
    den = compute_density_report(ev, mae_tol=e.density_mae_tol,
                                 min_scenarios=e.min_density_scenarios)
    rep = compute_replan_report(ev, cfg.routing.replan_deadline_s)
    lat = compute_latency(ev, e.alert_latency_p95_s)
    e2e_ok, e2e_note = check_end_to_end(ev, e.min_receiving_nodes)

    out = [
        Criterion("Packet delivery ratio", pdr.meets_target,
                  f"{pdr.overall_pdr:.3f} over {len(pdr.nodes)} nodes, {pdr.sent} alerts",
                  f">= {e.pdr_target:.2f} on >= {e.min_receiving_nodes} nodes",
                  "conservative (Wilson) test " +
                  ("passes" if pdr.meets_target_conservative else "does not pass")),
        Criterion("Density proxy vs manual count", den.meets_target,
                  f"MAE {den.mae:.2f} veh, {len(den.scenarios)} scenarios",
                  f"MAE <= {e.density_mae_tol} and busiest segment right, "
                  f">= {e.min_density_scenarios} scenarios",
                  "; ".join(f"{s.name}: MAE {s.mae:.2f}" for s in den.scenarios)),
        Criterion("Route recomputation time", rep.meets_target,
                  f"max {rep.max_s * 1e3:.2f} ms, {rep.replans} searches, "
                  f"{rep.route_changes} route changes",
                  f"<= {cfg.routing.replan_deadline_s} s and route adapts"),
        Criterion("End-to-end operation", e2e_ok, e2e_note,
                  "density -> replan -> alert -> >= min nodes"),
    ]
    if signal_id is not None:
        f = signal_id["full"]
        out.append(Criterion(
            "EV signal-ID false negative / positive", f.meets_target,
            f"FN {f.fn_rate:.4f} ({f.false_negatives}/{f.positives}), "
            f"FP {f.fp_rate:.4f} ({f.false_positives}/{f.negatives})",
            f"FN <= {f.max_fn}, FP <= {f.max_fp} (full decoder)",
            f"header-only: FN {signal_id['header'].fn_rate:.4f}, "
            f"FP {signal_id['header'].fp_rate:.4f}"))
    out.append(Criterion("Alert latency", lat.meets_target,
                         f"p95 {lat.p95_s * 1e3:.3f} ms, max {lat.max_s * 1e3:.3f} ms, "
                         f"{lat.samples} samples", f"p95 <= {lat.limit_s * 1e3:.0f} ms",
                         "software path only on loopback"))
    if trip is not None:
        ts = compute_time_saved(ev, cfg, *trip)
        out.append(Criterion(
            "Time saved vs static route (model)", ts.meets_target,
            f"{ts.saved_s:.1f} s ({ts.saved_pct:.1f} %) per trip over {ts.decisions} alerts",
            "adaptive route never costlier on average",
            f"{ts.worse_decisions} alerts where the static route was cheaper"))
    out.append(Criterion("Implementation feasibility", None, "manual review", "-",
                         "see docs/CAVEATS.md"))
    return out


def to_markdown(criteria: list[Criterion]) -> str:
    mark = {True: "PASS", False: "FAIL", None: "MANUAL"}
    rows = ["| Criterion | Result | Value | Target | Note |", "|---|---|---|---|---|"]
    rows += [f"| {c.name} | {mark[c.passed]} | {c.value} | {c.target} | {c.note} |"
             for c in criteria]
    return "\n".join(rows) + "\n"

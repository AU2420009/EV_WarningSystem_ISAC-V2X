"""Density proxy vs manual ground truth.

Criterion (report, Table II): the density proxy tracks manually counted vehicles in at
least ``eval.min_density_scenarios`` scenarios.

Log contract (one JSONL log, see ``scripts/run_demo.py``):

  ``truth``    scenario=<name>, at=<time>, counts={segment: vehicles}   (manual count)
  ``density``  at=<time>, counts={segment: estimate}                    (from feed())

A ``density`` event is paired with the ``truth`` event carrying the same ``at``. Within a
scenario the first ``warmup_frac`` of the pairs is dropped (the beacon TTL and the EMA
need time to settle after the traffic changes), then truth and estimate are averaged per
segment. Only segments that appear in both (i.e. segments that are sensed) are scored.

A scenario passes when its mean absolute error is within ``mae_tol`` vehicles AND the
busiest segment is identified correctly (what routing actually needs).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class SegmentError:
    segment: str
    truth: float
    estimate: float

    @property
    def error(self) -> float:
        return self.estimate - self.truth


@dataclass(frozen=True)
class ScenarioDensity:
    name: str
    segments: tuple[SegmentError, ...]
    samples: int                         # (truth, estimate) pairs after warm-up
    mae: float
    rmse: float
    max_abs_error: float
    busiest_ok: bool                     # estimate's busiest segment is a truth-busiest one
    passed: bool


@dataclass(frozen=True)
class DensityReport:
    scenarios: tuple[ScenarioDensity, ...]
    mae: float                           # pooled over all scored segments
    mae_tol: float
    min_scenarios: int
    meets_target: bool


def _score(name: str, truth: Mapping[str, float], est: Mapping[str, float],
           samples: int, mae_tol: float) -> ScenarioDensity:
    segs = tuple(SegmentError(s, truth[s], est[s]) for s in truth if s in est)
    if not segs:
        return ScenarioDensity(name, (), samples, math.nan, math.nan, math.nan, False, False)
    errs = [abs(x.error) for x in segs]
    mae = sum(errs) / len(errs)
    top_truth = max(x.truth for x in segs)
    best_est = max(segs, key=lambda x: x.estimate)
    busiest_ok = best_est.truth >= top_truth - 1e-9
    return ScenarioDensity(name, segs, samples, mae,
                           math.sqrt(sum(e * e for e in errs) / len(errs)), max(errs),
                           busiest_ok, mae <= mae_tol and busiest_ok)


def compute_density_report(events: Iterable[Mapping], *, warmup_frac: float = 0.5,
                           mae_tol: float = 1.0, min_scenarios: int = 2) -> DensityReport:
    if not 0.0 <= warmup_frac < 1.0:
        raise ValueError("warmup_frac must be in [0, 1)")
    truth_at: dict[float, tuple[str, Mapping[str, float]]] = {}
    order: dict[str, list[float]] = {}
    estimates: dict[float, Mapping[str, float]] = {}
    for e in events:
        if e.get("event") == "truth":
            truth_at[e["at"]] = (e["scenario"], e["counts"])
            order.setdefault(e["scenario"], []).append(e["at"])
        elif e.get("event") == "density" and e.get("counts") is not None:
            estimates[e["at"]] = e["counts"]

    scenarios = []
    for name, times in order.items():
        pairs = [t for t in sorted(times) if t in estimates]
        kept = pairs[int(len(pairs) * warmup_frac):]
        if not kept:
            scenarios.append(_score(name, {}, {}, 0, mae_tol))
            continue
        segs = [s for s in truth_at[kept[0]][1] if all(s in estimates[t] for t in kept)]
        truth = {s: sum(truth_at[t][1][s] for t in kept) / len(kept) for s in segs}
        est = {s: sum(estimates[t][s] for t in kept) / len(kept) for s in segs}
        scenarios.append(_score(name, truth, est, len(kept), mae_tol))

    scored = [x for sc in scenarios for x in sc.segments]
    pooled = (sum(abs(x.error) for x in scored) / len(scored)) if scored else math.nan
    return DensityReport(
        tuple(scenarios), pooled, mae_tol, min_scenarios,
        len(scenarios) >= min_scenarios and all(s.passed for s in scenarios))

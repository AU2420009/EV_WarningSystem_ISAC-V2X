"""Common interface for density estimators.

An estimator turns *something observed* (synthetic schedule, beacons, radar peaks) into a
vehicle count per road segment. The routing layer never sees how the numbers were
produced; it only receives ``DensityEstimate.counts``.

Segments no node senses are left OUT of ``counts`` (unknown is not the same as empty);
RoadGraph keeps its previous value for any segment that is not updated.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

from isacv2x.common.config import SystemConfig
from isacv2x.common.logging_utils import EventLogger

if TYPE_CHECKING:                                   # avoid a hard routing dependency
    from isacv2x.routing.replanner import ReplanResult, Replanner


@dataclass(frozen=True)
class DensityEstimate:
    counts: Mapping[str, float]       # estimated vehicles per covered segment
    timestamp: float                  # the ``now`` this estimate is for (seconds)
    source: str                       # estimator name, e.g. "beacon_counter"
    # Optional: mean speed (m/s) per segment, only for segments with speed samples.
    mean_speed_mps: Mapping[str, float] = field(default_factory=dict)


class DensityEstimator(ABC):
    name: str = "estimator"

    @abstractmethod
    def estimate(self, now: float) -> DensityEstimate:
        """Return the current per-segment vehicle estimate at time ``now`` (seconds)."""

    def start(self) -> None:                        # sockets/threads for live backends
        """Begin background collection. No-op for offline estimators."""

    def stop(self) -> None:
        """Release resources. No-op for offline estimators."""

    def __enter__(self) -> "DensityEstimator":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.stop()


class EmaSmoother:
    """Exponential moving average per segment: new = a*measurement + (1-a)*previous.

    alpha = 1.0 disables smoothing. A segment's first measurement initialises it directly
    (no bias towards zero).
    """

    def __init__(self, alpha: float):
        if not 0.0 < alpha <= 1.0:
            raise ValueError(f"alpha must be in (0, 1], got {alpha}")
        self.alpha = alpha
        self._state: dict[str, float] = {}

    def update(self, counts: Mapping[str, float]) -> dict[str, float]:
        out: dict[str, float] = {}
        for seg, c in counts.items():
            prev = self._state.get(seg)
            val = float(c) if prev is None else self.alpha * c + (1.0 - self.alpha) * prev
            self._state[seg] = out[seg] = val
        return out

    def reset(self) -> None:
        self._state.clear()


def covered_segments(cfg: SystemConfig) -> tuple[str, ...]:
    """Segments that have at least one sensing/relay node (config order)."""
    sensed = {n.segment for n in cfg.nodes}
    return tuple(s for s in cfg.segments if s in sensed)


def feed(estimator: DensityEstimator, replanner: "Replanner", now: float,
         logger: EventLogger | None = None) -> tuple[DensityEstimate, "ReplanResult"]:
    """One sensing->routing step: estimate density, hand it to the replanner."""
    est = estimator.estimate(now)
    if logger is not None:
        logger.log("density", source=est.source, at=est.timestamp, counts=dict(est.counts),
                   mean_speed_mps=dict(est.mean_speed_mps))
    return est, replanner.update_counts(est.counts)

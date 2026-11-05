"""Scripted density maps for testing routing and evaluation without any radio.

Density is defined by keyframes ``(time_s, {segment: vehicles})`` and linearly
interpolated; before the first keyframe the first values hold, after the last the last
values hold. Segments never mentioned stay at 0. Optional Gaussian noise models a
measurement error; ``truth(now)`` always returns the noiseless value, which doubles as the
ground truth for the density-validation evaluation.
"""
from __future__ import annotations

import bisect
import random
from typing import Mapping, Sequence

from isacv2x.common.config import SystemConfig
from isacv2x.density.base import DensityEstimate, DensityEstimator, EmaSmoother

Keyframe = tuple[float, Mapping[str, float]]


class SyntheticDensity(DensityEstimator):
    name = "synthetic"

    def __init__(self, cfg: SystemConfig, keyframes: Sequence[Keyframe], *,
                 noise_std: float = 0.0, seed: int | None = None, t0: float = 0.0,
                 smooth: bool = False):
        if not keyframes:
            raise ValueError("need at least one keyframe")
        times = [t for t, _ in keyframes]
        if any(b <= a for a, b in zip(times, times[1:])):
            raise ValueError("keyframe times must be strictly increasing")
        if noise_std < 0:
            raise ValueError("noise_std must be >= 0")
        self._segments = tuple(cfg.segments)
        for _, counts in keyframes:
            for seg, c in counts.items():
                if seg not in cfg.segments:
                    raise KeyError(f"unknown segment '{seg}' in keyframe")
                if c < 0:
                    raise ValueError(f"negative count for {seg}")
        self._times = times
        self._frames = [{s: float(c.get(s, 0.0)) for s in self._segments}
                        for _, c in keyframes]
        self._noise = noise_std
        self._rng = random.Random(cfg.seed if seed is None else seed)
        self._t0 = t0
        self._smoother = EmaSmoother(cfg.density.smoothing_alpha) if smooth else None

    # --------------------------------------------------------------- schedule
    def truth(self, now: float) -> dict[str, float]:
        """Noiseless vehicle count per segment at ``now``."""
        t = now - self._t0
        i = bisect.bisect_right(self._times, t)
        if i == 0:
            return dict(self._frames[0])
        if i == len(self._times):
            return dict(self._frames[-1])
        t_a, t_b = self._times[i - 1], self._times[i]
        w = (t - t_a) / (t_b - t_a)
        a, b = self._frames[i - 1], self._frames[i]
        return {s: a[s] + w * (b[s] - a[s]) for s in self._segments}

    def estimate(self, now: float) -> DensityEstimate:
        counts = self.truth(now)
        if self._noise > 0:
            counts = {s: max(0.0, c + self._rng.gauss(0.0, self._noise))
                      for s, c in counts.items()}
        if self._smoother is not None:
            counts = self._smoother.update(counts)
        return DensityEstimate(counts, now, self.name)

    # ------------------------------------------------------------ constructors
    @classmethod
    def static(cls, cfg: SystemConfig, counts: Mapping[str, float], **kw) -> "SyntheticDensity":
        return cls(cfg, [(0.0, counts)], **kw)

    @classmethod
    def congestion_event(cls, cfg: SystemConfig, segment: str, *, t_start: float,
                         t_peak: float, t_end: float, peak: float,
                         base: Mapping[str, float] | None = None,
                         **kw) -> "SyntheticDensity":
        """Congestion builds on one segment from t_start to t_peak, then clears by t_end."""
        if not t_start < t_peak < t_end:
            raise ValueError("need t_start < t_peak < t_end")
        base = dict(base or {})
        low = {**base, segment: base.get(segment, 0.0)}
        high = {**base, segment: peak}
        return cls(cfg, [(t_start, low), (t_peak, high), (t_end, low)], **kw)

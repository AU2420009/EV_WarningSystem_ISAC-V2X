"""Build the density estimator named by ``density.method`` in system.yaml."""
from __future__ import annotations

from typing import Sequence

from isacv2x.common.config import SystemConfig
from isacv2x.density.base import DensityEstimator
from isacv2x.density.beacon_counter import BeaconCounter
from isacv2x.density.synthetic import Keyframe, SyntheticDensity


def make_estimator(cfg: SystemConfig, *, keyframes: Sequence[Keyframe] | None = None,
                   noise_std: float = 0.0) -> DensityEstimator:
    method = cfg.density.method
    if method == "beacon_counter":
        return BeaconCounter(cfg)
    if method == "synthetic":
        if not keyframes:
            raise ValueError("density.method 'synthetic' needs keyframes")
        return SyntheticDensity(cfg, keyframes, noise_std=noise_std, smooth=True)
    if method == "radar_counter":
        try:
            from isacv2x.density.radar_counter import RadarCounter
        except ImportError as e:
            raise ImportError("density.method 'radar_counter' needs numpy "
                              "(pip install isac-v2x[radar])") from e
        return RadarCounter(cfg)
    raise ValueError(f"unknown density.method '{method}'")

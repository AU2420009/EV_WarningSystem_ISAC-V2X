"""Poisson traffic arrival generator and density estimator.

Models vehicle arrivals at road segments as a Poisson point process where the
time headway (inter-arrival time) follows an exponential distribution:
    delta_t = -ln(U) / lambda_rate

This provides a mathematically grounded stream of vehicle beacons for hardware
over-the-air USRP testing and bench evaluation.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Mapping, Sequence

from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.density.base import DensityEstimate, DensityEstimator, EmaSmoother


@dataclass
class PoissonVehicle:
    vehicle_id: int
    segment: str
    offset_m: float
    speed_mps: float
    entry_time_s: float
    last_beacon_s: float = 0.0


class PoissonTrafficGenerator:
    """Generates realistic vehicle traffic according to Poisson arrival processes."""

    def __init__(self, cfg: SystemConfig, *, arrival_rates: Mapping[str, float] | None = None,
                 speed_mps: float = 10.0, beacon_hz: float = 2.0, seed: int | None = None):
        self.cfg = cfg
        self._rng = random.Random(cfg.seed if seed is None else seed)
        self.speed_mps = speed_mps
        self.beacon_hz = beacon_hz
        self.beacon_interval_s = 1.0 / max(0.1, beacon_hz)

        # Default arrival rate: 0.2 veh/sec per segment (~1 car every 5 seconds)
        self.arrival_rates = dict(arrival_rates or {s: 0.2 for s in cfg.segments})
        self.vehicles: dict[int, PoissonVehicle] = {}
        self._next_arrival: dict[str, float] = {s: self._sample_headway(s) for s in cfg.segments}
        self._next_id = 1
        self._seq = 0

    def _sample_headway(self, segment: str) -> float:
        rate = max(0.0001, self.arrival_rates.get(segment, 0.1))
        # Inverse transform sampling of Exponential distribution: -ln(U) / lambda
        u = max(1e-9, self._rng.random())
        return -math.log(u) / rate

    def set_arrival_rate(self, segment: str, rate_veh_per_s: float) -> None:
        """Update arrival rate for a road segment (e.g., to create a traffic jam)."""
        self.arrival_rates[segment] = max(0.0, rate_veh_per_s)

    def set_rates(self, rates: Mapping[str, float]) -> None:
        for s, r in rates.items():
            self.set_arrival_rate(s, r)

    def step(self, now: float, dt: float) -> list[P.Beacon]:
        """Advance time by dt seconds, moving cars and emitting periodic beacons."""
        beacons: list[P.Beacon] = []

        # 1. Spawn new vehicle arrivals per segment
        for seg in self.cfg.segments:
            next_t = self._next_arrival.get(seg, now)
            if now >= next_t:
                vid = self._next_id
                self._next_id += 1
                spd = max(3.0, self.speed_mps + self._rng.gauss(0.0, 1.5))
                v = PoissonVehicle(vid, seg, offset_m=0.0, speed_mps=spd, entry_time_s=now, last_beacon_s=now)
                self.vehicles[vid] = v
                self._next_arrival[seg] = now + self._sample_headway(seg)

                self._seq += 1
                beacons.append(P.Beacon(
                    vehicle_id=v.vehicle_id,
                    seq=self._seq,
                    segment=v.segment,
                    offset_m=v.offset_m,
                    speed_mps=v.speed_mps,
                    timestamp_us=int(now * 1e6)
                ))

        # 2. Advance existing vehicles along segments and generate beacons
        dead = []
        seg_lens = {s: self.cfg.segments[s].length_m for s in self.cfg.segments}
        for vid, v in list(self.vehicles.items()):
            v.offset_m += v.speed_mps * dt
            max_len = seg_lens.get(v.segment, 400.0)
            if v.offset_m >= max_len:
                dead.append(vid)
            elif (now - v.last_beacon_s) >= self.beacon_interval_s:
                v.last_beacon_s = now
                self._seq += 1
                beacons.append(P.Beacon(
                    vehicle_id=v.vehicle_id,
                    seq=self._seq,
                    segment=v.segment,
                    offset_m=round(v.offset_m, 2),
                    speed_mps=round(v.speed_mps, 2),
                    timestamp_us=int(now * 1e6)
                ))

        for vid in dead:
            del self.vehicles[vid]

        return beacons

    def active_counts(self) -> dict[str, int]:
        """True instantaneous vehicle count on each road segment."""
        counts = {s: 0 for s in self.cfg.segments}
        for v in self.vehicles.values():
            if v.segment in counts:
                counts[v.segment] += 1
        return counts


class PoissonDensityEstimator(DensityEstimator):
    """DensityEstimator backed by a PoissonTrafficGenerator."""
    name = "poisson"

    def __init__(self, cfg: SystemConfig, *, arrival_rates: Mapping[str, float] | None = None,
                 speed_mps: float = 10.0, seed: int | None = None):
        self.generator = PoissonTrafficGenerator(cfg, arrival_rates=arrival_rates,
                                                 speed_mps=speed_mps, seed=seed)
        self._last_t = 0.0
        self._smoother = EmaSmoother(cfg.density.smoothing_alpha)

    def estimate(self, now: float) -> DensityEstimate:
        dt = max(0.01, now - self._last_t) if self._last_t > 0 else 0.5
        self.generator.step(now, dt)
        self._last_t = now
        counts = {s: float(c) for s, c in self.generator.active_counts().items()}
        counts = self._smoother.update(counts)
        return DensityEstimate(counts, now, self.name)


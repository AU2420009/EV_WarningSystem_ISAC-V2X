"""The report's density proxy: count active beacons per road segment.

Each vehicle periodically broadcasts a Beacon. Sensing/relay nodes feed every beacon they
decode into ``ingest``/``ingest_bytes``. A vehicle is *active* if its newest beacon was
received within ``density.beacon_ttl_s``; the estimate for a segment is the number of
active vehicles whose newest beacon reports that segment.

* Vehicles are keyed by ``vehicle_id``, so one vehicle heard by several nodes counts once,
  and a vehicle that changes segment moves its count with it.
* Freshness uses the *receive* time passed in by the caller, not the beacon's own
  timestamp, so vehicle clock skew cannot make a beacon look fresh or stale.
* Only segments with a sensing node are reported (see density.base.covered_segments).
* This class does no socket I/O; the relay/receiver layer owns the radios or UDP sockets
  and calls ``ingest_bytes``. That keeps it fully testable offline.

Mean speed per segment uses the beacon's reported speed when present, otherwise speed
derived from consecutive beacons of the same vehicle on the same segment.
"""
from __future__ import annotations

import math
import threading
from dataclasses import dataclass

from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.density.base import (DensityEstimate, DensityEstimator, EmaSmoother,
                                  covered_segments)


@dataclass
class _Track:
    last: P.Beacon
    rx_time: float
    node_id: str | None
    prev: P.Beacon | None = None


class BeaconCounter(DensityEstimator):
    name = "beacon_counter"

    def __init__(self, cfg: SystemConfig, *, ttl_s: float | None = None,
                 smoothing_alpha: float | None = None):
        self._preamble = cfg.alert.preamble
        self._max_bytes = cfg.alert.max_payload_bytes
        self._ttl = cfg.density.beacon_ttl_s if ttl_s is None else ttl_s
        if self._ttl <= 0:
            raise ValueError("ttl_s must be > 0")
        self._segments = set(cfg.segments)
        self._covered = covered_segments(cfg)
        alpha = cfg.density.smoothing_alpha if smoothing_alpha is None else smoothing_alpha
        self._smoother = EmaSmoother(alpha)
        self._tracks: dict[int, _Track] = {}
        self._lock = threading.Lock()
        self.stats: dict[str, int] = {
            "ok": 0, "stale": 0, "unknown_segment": 0, "bad_frame": 0,
            "not_beacon": 0, "uncovered_segment": 0,
        }

    # ------------------------------------------------------------------ input
    def ingest(self, beacon: P.Beacon, rx_time: float, node_id: str | None = None) -> str:
        """Record one decoded beacon. Returns 'ok', 'stale' or 'unknown_segment'."""
        if beacon.segment not in self._segments:
            return self._count("unknown_segment")
        with self._lock:
            old = self._tracks.get(beacon.vehicle_id)
            stale = old is not None and beacon.timestamp_us <= old.last.timestamp_us
            if not stale:
                prev = old.last if old is not None else None
                self._tracks[beacon.vehicle_id] = _Track(beacon, rx_time, node_id, prev)
        # _count takes the (non-reentrant) lock itself, so call it after releasing
        return self._count("stale" if stale else "ok")

    def ingest_bytes(self, data: bytes, rx_time: float, node_id: str | None = None) -> str:
        """Decode a raw frame and ingest it if it is a valid Beacon.

        Returns 'ok', 'stale', 'unknown_segment', 'bad_frame' (preamble/CRC/length
        failure) or 'not_beacon' (valid frame of another type, e.g. an Alert).
        """
        try:
            msg = P.decode(data, self._preamble)
        except P.PacketError:
            return self._count("bad_frame")
        if not isinstance(msg, P.Beacon):
            return self._count("not_beacon")
        return self.ingest(msg, rx_time, node_id)

    def _count(self, key: str) -> str:
        with self._lock:
            self.stats[key] += 1
        return key

    # ----------------------------------------------------------------- output
    def prune(self, now: float) -> int:
        """Forget vehicles not heard within the TTL. Returns how many were removed."""
        with self._lock:
            dead = [v for v, t in self._tracks.items() if now - t.rx_time > self._ttl]
            for v in dead:
                del self._tracks[v]
            return len(dead)

    def active_vehicles(self, now: float) -> dict[int, str]:
        """vehicle_id -> segment for every currently active vehicle."""
        with self._lock:
            return {v: t.last.segment for v, t in self._tracks.items()
                    if 0.0 <= now - t.rx_time <= self._ttl}

    def estimate(self, now: float) -> DensityEstimate:
        self.prune(now)
        counts = {s: 0.0 for s in self._covered}
        speeds: dict[str, list[float]] = {}
        with self._lock:
            for t in self._tracks.values():
                if now < t.rx_time:                  # beacon "from the future": ignore
                    continue
                seg = t.last.segment
                if seg not in counts:
                    self.stats["uncovered_segment"] += 1
                    continue
                counts[seg] += 1.0
                v = self._speed_of(t)
                if v is not None:
                    speeds.setdefault(seg, []).append(v)
        smoothed = self._smoother.update(counts)
        mean_speed = {s: sum(v) / len(v) for s, v in speeds.items()}
        return DensityEstimate(smoothed, now, self.name, mean_speed)

    @staticmethod
    def _speed_of(t: _Track) -> float | None:
        if t.last.has_speed and t.last.speed_mps >= 0.0:
            return t.last.speed_mps
        if t.prev is not None:
            v = P.speed_from_beacons(t.prev, t.last)
            if v is not None and math.isfinite(v):
                return v
        return None

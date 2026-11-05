# The radar counting system is incomplete and should not be considered ready for use.
"""Optional second density estimator: count moving targets in OpenISAC range-Doppler maps.

Unlike the beacon counter this needs no cooperation from the vehicle, so unequipped
traffic is counted too. It is the only place OpenISAC's sensing is used.

Pipeline per range-Doppler power map (one per BS sensing frame, rows = Doppler, columns =
range, Doppler FFT-shifted so row ``n_doppler // 2`` is zero Doppler):

  1. gate out |v| < ``min_speed_mps`` (OpenISAC's MTI already notches zero Doppler; this
     removes the notch skirts and any static-clutter residue),
  2. 2-D cell-averaging CFAR (``cfar_guard``, ``cfar_train``, ``pfa``),
  3. keep local maxima (one detection per target peak),
  4. convert bins: range = bin * range_bin_m + range_offset_m, v = c * f_D / (2 f_c).

Per node, the count for its segment is the mean number of detections inside the segment
over the frames of the last ``window_s`` seconds; that goes through the usual EMA smoother.
A node with no recent frames is left out of the estimate (unknown is not empty).

What is documented vs assumed (docs/CAVEATS.md section 4):
  * documented: f_D = k_f / (M T_slow), v = c f_D / (2 f_c), range resolution c / (2B),
    MTI as a zero-Doppler notch.
  * NOT documented: the ZMQ wire format (the viewer requires a "PARM V9" layout). Decoding
    bytes into a power map is therefore a caller-supplied ``decode_frame`` function.
  * ASSUMED here: ``doppler_bin_hz`` and ``range_bin_m`` come from RadarParams (set them
    from your BS YAML: sensing.range_fft_size, doppler_fft_size, symbol_stride).

Limits that remain whatever the settings: vehicles closer than the range resolution with
similar speed merge into one peak (under-count in dense traffic), and vehicles slower than
``min_speed_mps`` are invisible, so a standstill queue reads as empty.

Needs numpy (``pip install isac-v2x[radar]``); live ZMQ input also needs pyzmq.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Callable, Sequence

from isacv2x.backends.openisac import require_zmq
from isacv2x.common.config import SystemConfig
from isacv2x.density.base import DensityEstimate, DensityEstimator, EmaSmoother

try:
    import numpy as np
except ImportError as e:                                  # pragma: no cover
    raise ImportError("radar_counter needs numpy: pip install numpy") from e

C = 299_792_458.0


@dataclass(frozen=True)
class RadarParams:
    center_freq_hz: float
    range_bin_m: float                # metres per range column
    doppler_bin_hz: float             # Hz per Doppler row (1 / (M_s * T_slow))
    range_offset_m: float = 0.0       # radar-to-segment-start offset
    min_speed_mps: float = 1.0
    cfar_guard: tuple[int, int] = (1, 1)      # (doppler, range) guard half-sizes
    cfar_train: tuple[int, int] = (4, 4)      # extra training half-size beyond the guard
    pfa: float = 1e-5

    @classmethod
    def from_config(cls, cfg: SystemConfig, *, doppler_bin_hz: float = 8.0,
                    **kw) -> "RadarParams":
        """Defaults from system.yaml. range_bin_m = c / (2 * sample_rate) (7.5 m at 20 MHz).

        ``doppler_bin_hz`` = 8 Hz is about 0.5 m/s at 2.4 GHz; it is an assumption until
        read from the real frame/stride settings.
        """
        return cls(cfg.radio.center_freq_hz, C / (2.0 * cfg.radio.sample_rate_hz),
                   doppler_bin_hz, **kw)

    def velocity_of_row(self, row: int, n_doppler: int) -> float:
        return (row - n_doppler // 2) * self.doppler_bin_hz * C / (2.0 * self.center_freq_hz)

    def row_of_velocity(self, v: float, n_doppler: int) -> int:
        return int(round(v * 2.0 * self.center_freq_hz / C / self.doppler_bin_hz)) + n_doppler // 2


@dataclass(frozen=True)
class Detection:
    range_m: float
    velocity_mps: float
    power: float


# ------------------------------------------------------------------- CFAR
def _box_sum(a, hd: int, hr: int):
    """Sum of ``a`` over a (2hd+1) x (2hr+1) window around each cell, clipped at edges."""
    h, w = a.shape
    c = np.pad(a.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    i0 = np.clip(np.arange(h) - hd, 0, h); i1 = np.clip(np.arange(h) + hd + 1, 0, h)
    j0 = np.clip(np.arange(w) - hr, 0, w); j1 = np.clip(np.arange(w) + hr + 1, 0, w)
    return c[i1][:, j1] - c[i0][:, j1] - c[i1][:, j0] + c[i0][:, j0]


def cfar_2d(power, guard: tuple[int, int], train: tuple[int, int], pfa: float):
    """Boolean detection mask from cell-averaging CFAR (training ring = outer - guard box)."""
    gd, gr = guard
    od, orr = gd + train[0], gr + train[1]
    ones = np.ones_like(power)
    n = _box_sum(ones, od, orr) - _box_sum(ones, gd, gr)
    noise = (_box_sum(power, od, orr) - _box_sum(power, gd, gr)) / np.maximum(n, 1.0)
    alpha = n * (pfa ** (-1.0 / np.maximum(n, 1.0)) - 1.0)
    return (n > 0) & (power > alpha * noise)


def _local_max(power):
    p = np.pad(power, 1, constant_values=-np.inf)
    h, w = power.shape
    out = np.ones_like(power, dtype=bool)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            if di or dj:
                out &= power >= p[1 + di:1 + di + h, 1 + dj:1 + dj + w]
    return out


def detect(power, params: RadarParams) -> list[Detection]:
    """Moving-target detections in one range-Doppler power map."""
    power = np.asarray(power, dtype=float)
    if power.ndim != 2:
        raise ValueError("power map must be 2-D (doppler x range)")
    n_d = power.shape[0]
    mask = cfar_2d(power, params.cfar_guard, params.cfar_train, params.pfa) & _local_max(power)
    rows = np.arange(n_d)
    v = (rows - n_d // 2) * params.doppler_bin_hz * C / (2.0 * params.center_freq_hz)
    mask &= (np.abs(v) >= params.min_speed_mps)[:, None]
    out = []
    for i, j in zip(*np.nonzero(mask)):
        out.append(Detection(j * params.range_bin_m + params.range_offset_m,
                             float(v[i]), float(power[i, j])))
    return out


def synth_frame(params: RadarParams, targets: Sequence[tuple[float, float, float]], *,
                n_doppler: int = 100, n_range: int = 64, noise_power: float = 1.0,
                rng=None):
    """Synthetic power map: exponential noise plus one peak per (range_m, v_mps, snr_db)."""
    rng = rng or np.random.default_rng(0)
    # |complex Gaussian|^2 is exponentially distributed
    p = rng.exponential(noise_power, size=(n_doppler, n_range))
    for r, v, snr_db in targets:
        j = int(round((r - params.range_offset_m) / params.range_bin_m))
        i = params.row_of_velocity(v, n_doppler)
        if 0 <= i < n_doppler and 0 <= j < n_range:
            p[i, j] += noise_power * 10 ** (snr_db / 10.0)
    return p


# --------------------------------------------------------------- estimator
class RadarCounter(DensityEstimator):
    name = "radar_counter"

    def __init__(self, cfg: SystemConfig, params: RadarParams | None = None, *,
                 window_s: float | None = None, smoothing_alpha: float | None = None,
                 decode_frame: Callable[[bytes], "np.ndarray"] | None = None):
        self._cfg = cfg
        self.params = params or RadarParams.from_config(cfg)
        self._window = cfg.density.beacon_ttl_s if window_s is None else window_s
        if self._window <= 0:
            raise ValueError("window_s must be > 0")
        self._node_seg = {n.id: n.segment for n in cfg.nodes}
        self._frames: dict[str, deque[tuple[float, int, list[float]]]] = {
            n: deque() for n in self._node_seg}
        self._smoother = EmaSmoother(cfg.density.smoothing_alpha
                                     if smoothing_alpha is None else smoothing_alpha)
        self._decode = decode_frame
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.stats = {"frames": 0, "detections": 0, "bad_frame": 0}

    # ------------------------------------------------------------------ input
    def ingest_frame(self, node_id: str, power, rx_time: float) -> int:
        """Process one range-Doppler power map from ``node_id``; returns detection count."""
        if node_id not in self._node_seg:
            raise KeyError(f"unknown node '{node_id}'")
        seg_len = self._cfg.segments[self._node_seg[node_id]].length_m
        dets = [d for d in detect(power, self.params)
                if 0.0 <= d.range_m <= seg_len + self.params.range_bin_m / 2]
        with self._lock:
            self._frames[node_id].append((rx_time, len(dets),
                                          [abs(d.velocity_mps) for d in dets]))
            self.stats["frames"] += 1
            self.stats["detections"] += len(dets)
        return len(dets)

    # ----------------------------------------------------------------- output
    def estimate(self, now: float) -> DensityEstimate:
        per_seg: dict[str, list[float]] = {}
        speeds: dict[str, list[float]] = {}
        with self._lock:
            for node, q in self._frames.items():
                while q and now - q[0][0] > self._window:
                    q.popleft()
                recent = [f for f in q if f[0] <= now]
                if not recent:
                    continue
                seg = self._node_seg[node]
                per_seg.setdefault(seg, []).append(sum(f[1] for f in recent) / len(recent))
                for f in recent:
                    speeds.setdefault(seg, []).extend(f[2])
        counts = self._smoother.update({s: sum(v) / len(v) for s, v in per_seg.items()})
        return DensityEstimate(counts, now, self.name,
                               {s: sum(v) / len(v) for s, v in speeds.items() if v})

    # ------------------------------------------------------------ live input
    def start(self, node_id: str | None = None) -> None:
        """Subscribe to the BS sensing PUB socket (UNTESTED against a real OpenISAC BS)."""
        if self._decode is None:
            raise NotImplementedError(
                "OpenISAC's sensing wire format (PARM V9 header) is not documented; pass "
                "decode_frame=<bytes -> 2-D power array> to RadarCounter")
        zmq = require_zmq()
        node = node_id or next(n.id for n in self._cfg.nodes if n.backend == "openisac")
        o = self._cfg.openisac
        sock = zmq.Context.instance().socket(zmq.SUB)
        sock.setsockopt(zmq.SUBSCRIBE, b"")
        sock.connect(f"tcp://{o.host}:{o.zmq_monostatic}")
        self._stop.clear()

        def run() -> None:
            import time
            poller = zmq.Poller()
            poller.register(sock, zmq.POLLIN)
            while not self._stop.is_set():
                if not dict(poller.poll(100)):
                    continue
                raw = sock.recv()
                try:
                    self.ingest_frame(node, self._decode(raw), time.monotonic())
                except Exception:                                  # noqa: BLE001
                    with self._lock:
                        self.stats["bad_frame"] += 1
            sock.close(0)

        self._thread = threading.Thread(target=run, name="radar-sub", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None

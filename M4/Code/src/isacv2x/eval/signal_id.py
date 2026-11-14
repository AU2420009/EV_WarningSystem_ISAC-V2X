"""EV signal identification: false-negative and false-positive rates.

Criterion (report, Table II): the EV alert must be recognised reliably (few missed alerts,
few non-EV frames mistaken for alerts).

Two identifiers are scored on the same labelled frames:

* ``header``  header-only check (``packets.peek_type``): preamble + version + type. Cheap,
              but accepts frames whose body was corrupted.
* ``full``    the receiver's decoder (``packets.decode``): header + length + CRC32. This is
              what ``AlertReceiver`` acts on, so it is the one the criterion is judged on.

Ground truth comes from construction: a frame is *positive* if it started life as one of
this system's EV alerts. ``make_corpus`` builds a labelled corpus from

  * positives        EV alerts (random routes),
  * negatives        beacons, alerts under a different preamble (another system), pure
                     random noise, and an alert-shaped frame with the wrong version byte,

and then flips bits at rate ``ber`` in every frame (a stand-in for channel errors that
slip past the PHY's own coding). Results on this corpus say how the *identifier* behaves;
they are not radio measurements. For radio data, label frames at the transmitter and
feed ``score`` the same way.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.eval.pdr import wilson_interval

Detector = Callable[[bytes], bool]
LabelledFrame = tuple[bytes, bool]          # (frame bytes, is_ev_alert)


def header_detector(preamble: bytes) -> Detector:
    return lambda data: P.peek_type(data, preamble) == P.TYPE_ALERT


def full_detector(preamble: bytes) -> Detector:
    def detect(data: bytes) -> bool:
        try:
            return isinstance(P.decode(data, preamble), P.Alert)
        except P.PacketError:
            return False
    return detect


@dataclass(frozen=True)
class SignalIdResult:
    detector: str
    positives: int
    negatives: int
    false_negatives: int
    false_positives: int
    fn_rate: float                       # FN / positives
    fp_rate: float                       # FP / negatives
    fn_ci: tuple[float, float]           # 95 % Wilson interval
    fp_ci: tuple[float, float]
    meets_target: bool                   # both point estimates within limits
    max_fn: float
    max_fp: float


def score(frames: Iterable[LabelledFrame], detector: Detector, name: str, *,
          max_fn: float = 0.05, max_fp: float = 0.01) -> SignalIdResult:
    pos = neg = fn = fp = 0
    for data, is_alert in frames:
        hit = detector(data)
        if is_alert:
            pos += 1
            fn += not hit
        else:
            neg += 1
            fp += hit
    fn_rate = fn / pos if pos else float("nan")
    fp_rate = fp / neg if neg else float("nan")
    ok = pos > 0 and neg > 0 and fn_rate <= max_fn and fp_rate <= max_fp
    return SignalIdResult(name, pos, neg, fn, fp, fn_rate, fp_rate,
                          wilson_interval(fn, pos), wilson_interval(fp, neg),
                          ok, max_fn, max_fp)


def flip_bits(data: bytes, ber: float, rng: random.Random) -> bytes:
    """Flip each bit independently with probability ``ber`` (seeded, reproducible)."""
    if ber <= 0:
        return data
    out = bytearray(data)
    for i in range(len(out) * 8):
        if rng.random() < ber:
            out[i // 8] ^= 1 << (i % 8)
    return bytes(out)


def make_corpus(cfg: SystemConfig, *, n_alerts: int = 2000, n_negatives: int = 2000,
                ber: float = 1e-4, seed: int | None = None) -> list[LabelledFrame]:
    """Labelled frames: EV alerts (positive) and several kinds of non-EV traffic."""
    if not 0.0 <= ber <= 1.0:
        raise ValueError("ber must be in [0, 1]")
    rng = random.Random(cfg.seed if seed is None else seed)
    pre, cap = cfg.alert.preamble, cfg.alert.max_payload_bytes
    segs = list(cfg.segments)
    other_pre = bytes(b ^ 0xFF for b in pre)
    frames: list[LabelledFrame] = []
    ts = 1_700_000_000_000_000          # fixed timestamps keep the corpus reproducible

    for i in range(n_alerts):
        route = tuple(rng.sample(segs, rng.randint(1, min(4, len(segs)))))
        frames.append((P.encode(P.Alert(cfg.alert.ev_id, cfg.alert.priority, i + 1, route, ts + i),
                                pre, cap), True))

    for i in range(n_negatives):
        kind = i % 4
        if kind == 0:                                   # ordinary beacon
            b = P.Beacon(rng.randrange(1, 10_000), i, rng.choice(segs),
                         rng.uniform(0, 300), rng.uniform(0, 25), ts + i)
            frames.append((P.encode(b, pre, cap), False))
        elif kind == 1:                                 # EV alert of another system
            a = P.Alert(rng.randrange(0, 65535), 1, i, (rng.choice(segs),), ts + i)
            frames.append((P.encode(a, other_pre, cap), False))
        elif kind == 2:                                 # random noise
            frames.append((bytes(rng.randrange(256) for _ in range(rng.randint(8, 64))),
                           False))
        else:                                           # alert-shaped, unsupported version
            a = bytearray(P.encode(P.Alert(cfg.alert.ev_id, 1, i, (rng.choice(segs),), ts + i),
                                   pre, cap))
            a[len(pre)] = P.VERSION + 1                 # version byte (CRC no longer valid)
            frames.append((bytes(a), False))

    return [(flip_bits(d, ber, rng), lab) for d, lab in frames]


def evaluate_signal_id(cfg: SystemConfig, frames: Sequence[LabelledFrame] | None = None,
                       **corpus_kw) -> dict[str, SignalIdResult]:
    """Score the header-only and full identifiers; the full one decides the criterion."""
    frames = list(frames) if frames is not None else make_corpus(cfg, **corpus_kw)
    kw = dict(max_fn=cfg.eval.signal_id_max_fn, max_fp=cfg.eval.signal_id_max_fp)
    return {
        "header": score(frames, header_detector(cfg.alert.preamble), "header", **kw),
        "full": score(frames, full_detector(cfg.alert.preamble), "full", **kw),
    }

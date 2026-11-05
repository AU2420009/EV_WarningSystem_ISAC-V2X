"""In-process lossy link for testing the alert path without any radio or OpenISAC binary.

``LoopbackChannel`` is one point-to-point link with seeded random loss, fixed delay and
optional jitter. It hands out a ``Sender`` and a ``Receiver`` (same interfaces as the UDP
backend), so alert/relay/evaluation code runs unchanged on top of it.

Loss is drawn once per sent datagram from a seeded RNG, so a run is reproducible.
Timing uses the real monotonic clock.
"""
from __future__ import annotations

import heapq
import random
import threading
import time

from isacv2x.backends.base import Datagram, Receiver, Sender


class LoopbackChannel:
    def __init__(self, *, loss: float = 0.0, delay_s: float = 0.0, jitter_s: float = 0.0,
                 seed: int = 0, max_bytes: int = Sender.max_bytes):
        if not 0.0 <= loss <= 1.0:
            raise ValueError("loss must be in [0, 1]")
        if delay_s < 0 or jitter_s < 0:
            raise ValueError("delay_s and jitter_s must be >= 0")
        if not 1 <= max_bytes <= Sender.max_bytes:
            raise ValueError(f"max_bytes must be in 1..{Sender.max_bytes}")
        self.loss, self.delay_s, self.jitter_s = loss, delay_s, jitter_s
        self.max_bytes = max_bytes
        self._rng = random.Random(seed)
        self._heap: list[tuple[float, int, bytes]] = []
        self._n = 0
        self._cond = threading.Condition()
        self.stats = {"sent": 0, "dropped": 0, "delivered": 0}

    def sender(self) -> Sender:
        return _LoopSender(self)

    def receiver(self) -> Receiver:
        return _LoopReceiver(self)

    # ---------------------------------------------------------------- internals
    def _put(self, data: bytes) -> None:
        with self._cond:
            self.stats["sent"] += 1
            if self._rng.random() < self.loss:
                self.stats["dropped"] += 1
                return
            delay = self.delay_s + (self._rng.uniform(0.0, self.jitter_s)
                                    if self.jitter_s else 0.0)
            self._n += 1
            heapq.heappush(self._heap, (time.monotonic() + delay, self._n, data))
            self._cond.notify_all()

    def _get(self, timeout: float | None) -> Datagram | None:
        deadline = None if timeout is None else time.monotonic() + max(timeout, 0.0)
        with self._cond:
            while True:
                now = time.monotonic()
                if self._heap and self._heap[0][0] <= now:
                    _, _, data = heapq.heappop(self._heap)
                    self.stats["delivered"] += 1
                    return Datagram(data, ("loopback", 0), now)
                if deadline is not None and now >= deadline:
                    return None
                waits = []
                if self._heap:
                    waits.append(self._heap[0][0] - now)
                if deadline is not None:
                    waits.append(deadline - now)
                self._cond.wait(max(min(waits), 0.0005) if waits else None)


class _LoopSender(Sender):
    def __init__(self, ch: LoopbackChannel):
        self._ch, self.max_bytes = ch, ch.max_bytes
        self._closed = False

    def send(self, data: bytes) -> int:
        self._check_size(data)
        if self._closed:
            raise RuntimeError("sender is closed")
        self._ch._put(bytes(data))
        return len(data)

    def close(self) -> None:
        self._closed = True


class _LoopReceiver(Receiver):
    def __init__(self, ch: LoopbackChannel):
        self._ch = ch
        self._closed = False

    def recv(self, timeout: float | None = None) -> Datagram | None:
        if self._closed:
            raise RuntimeError("receiver is closed")
        return self._ch._get(timeout)

    def close(self) -> None:
        self._closed = True

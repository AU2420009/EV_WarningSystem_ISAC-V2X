"""Vehicle/node side: identify EV alerts, validate, de-duplicate, decide relevance.

Every received datagram gets exactly one outcome:

  ok         new, valid alert (an AlertEvent is returned in ``Outcome.event``)
  duplicate  valid alert already seen (a repeat copy)
  not_ev     header does not look like an EV frame (preamble/version/type mismatch)
  corrupt    header matched but full validation failed (CRC/length): "identified" but bad
  not_alert  valid frame of another type, e.g. a Beacon

``not_ev`` vs ``corrupt`` is the signal-identification detail used by eval/signal_id.py:
a header-only detector would accept ``corrupt`` frames, the full decoder rejects them.

``on_route`` says whether this node's segment is on the alert's route. Alerts are still
reported (so evaluation can see them) but a node should only *act* when on_route is True.
"""
from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable

from isacv2x.backends.base import Receiver
from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.common.logging_utils import EventLogger


@dataclass(frozen=True)
class AlertEvent:
    alert: P.Alert
    node_id: str
    on_route: bool
    latency_s: float          # receive time minus alert.timestamp_us (same-host/NTP clocks)
    rx_time: float            # monotonic receive time


@dataclass(frozen=True)
class Outcome:
    reason: str
    event: AlertEvent | None = None


class AlertReceiver:
    def __init__(self, cfg: SystemConfig, link: Receiver, node_id: str, *,
                 segment: str | None = None, logger: EventLogger | None = None,
                 now_us: Callable[[], int] = P.now_us, dedup_size: int = 256):
        if segment is not None and segment not in cfg.segments:
            raise KeyError(f"unknown segment '{segment}'")
        if dedup_size < 1:
            raise ValueError("dedup_size must be >= 1")
        self._pre = cfg.alert.preamble
        self._link, self.node_id, self.segment = link, node_id, segment
        self._log, self._now_us = logger, now_us
        self._seen: OrderedDict[tuple[int, int], None] = OrderedDict()
        self._dedup_size = dedup_size
        self.stats = {"rx": 0, "ok": 0, "duplicate": 0, "not_ev": 0,
                      "corrupt": 0, "not_alert": 0}

    def poll(self, timeout: float | None = 0.0) -> Outcome | None:
        """Process at most one datagram. Returns None only if nothing arrived in time."""
        dgram = self._link.recv(timeout)
        if dgram is None:
            return None
        self.stats["rx"] += 1
        out = self._classify(dgram.data, dgram.rx_time)
        self.stats[out.reason] += 1
        if self._log is not None:
            ev = out.event
            self._log.log("alert_rx", node=self.node_id, reason=out.reason,
                          ev_id=ev.alert.ev_id if ev else None,
                          seq=ev.alert.seq if ev else None,
                          on_route=ev.on_route if ev else None,
                          latency_s=ev.latency_s if ev else None, bytes=len(dgram.data))
        return out

    def drain(self) -> list[Outcome]:
        """Process everything currently queued (non-blocking)."""
        outs = []
        while (o := self.poll(0.0)) is not None:
            outs.append(o)
        return outs

    # ---------------------------------------------------------------- internals
    def _classify(self, data: bytes, rx_time: float) -> Outcome:
        if P.peek_type(data, self._pre) is None:
            return Outcome("not_ev")
        try:
            msg = P.decode(data, self._pre)
        except P.PacketError:
            return Outcome("corrupt")
        if not isinstance(msg, P.Alert):
            return Outcome("not_alert")

        key = (msg.ev_id, msg.seq)
        if key in self._seen:
            self._seen.move_to_end(key)
            return Outcome("duplicate")
        self._seen[key] = None
        if len(self._seen) > self._dedup_size:
            self._seen.popitem(last=False)

        on_route = self.segment is None or self.segment in msg.route
        latency = (self._now_us() - msg.timestamp_us) / 1e6
        return Outcome("ok", AlertEvent(msg, self.node_id, on_route, latency, rx_time))

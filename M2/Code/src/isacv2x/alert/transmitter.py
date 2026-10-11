"""EV side: build the make-way alert for the chosen route and send it.

The alert carries the route (segment ids) so only nodes on that route react. Because the
broadcast has no per-receiver ACK (report design), reliability comes from sending each
alert ``alert.repeat`` times, ``alert.repeat_gap_s`` apart. All repeats are byte-identical
(same sequence number); receivers de-duplicate on (ev_id, seq).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Sequence

from isacv2x.backends.base import Sender
from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.common.logging_utils import EventLogger


@dataclass(frozen=True)
class SentAlert:
    alert: P.Alert
    frame: bytes              # exact bytes put on the link
    copies: int               # how many copies were sent


class AlertTransmitter:
    def __init__(self, cfg: SystemConfig, sender: Sender, *,
                 logger: EventLogger | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self._cfg = cfg.alert
        self._sender = sender
        self._log = logger
        self._sleep = sleep
        self._seq = 0

    def send_alert(self, route: Sequence[str] | object) -> SentAlert:
        """Send an alert for ``route`` (a Route from the planner, or a list of segment ids)."""
        segments = tuple(getattr(route, "segments", route))
        self._seq = (self._seq % 0xFFFFFFFF) + 1                 # 1..2^32-1, wraps
        alert = P.Alert(self._cfg.ev_id, self._cfg.priority, self._seq, segments)
        frame = P.encode(alert, self._cfg.preamble, self._cfg.max_payload_bytes)

        for i in range(self._cfg.repeat):
            if i:
                self._sleep(self._cfg.repeat_gap_s)
            self._sender.send(frame)
            if self._log is not None:
                self._log.log("alert_tx", ev_id=alert.ev_id, seq=alert.seq,
                              priority=alert.priority, route=segments, copy=i + 1,
                              copies=self._cfg.repeat, bytes=len(frame),
                              sent_us=alert.timestamp_us)
        return SentAlert(alert, frame, self._cfg.repeat)

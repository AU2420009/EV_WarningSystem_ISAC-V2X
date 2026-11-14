"""Virtual relay / fan-out node.

OpenISAC links one transmitter to one receiver, so reaching 3+ receiving nodes (report
criterion) is done in software: the relay listens on one link and re-sends frames to
several output links. A relay is also a sensing node: beacons it hears can be fed to a
BeaconCounter.

Behaviour per received datagram:
  * valid Alert whose route contains this relay's segment -> forwarded VERBATIM to every
    output (same bytes, so repeat copies and CRC survive); ``route_filter=False`` forwards
    every valid alert regardless of route.
  * valid Alert not covering this segment -> counted as off_route, not forwarded.
  * Beacon -> given to ``beacon_sink`` (if any), never forwarded.
  * anything else, or a frame that fails CRC -> dropped (corruption is not propagated).

All repeat copies are forwarded (receivers de-duplicate). Relays must form a star or tree:
never connect a relay's output back to a relay input, because frames carry no TTL.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from isacv2x.backends.base import Receiver, Sender
from isacv2x.common import packets as P
from isacv2x.common.config import SystemConfig
from isacv2x.common.logging_utils import EventLogger
from isacv2x.density.beacon_counter import BeaconCounter


@dataclass(frozen=True)
class RelayOutcome:
    reason: str               # forwarded | off_route | beacon | invalid | ignored
    delivered_to: int = 0     # outputs that accepted the frame


class Relay:
    def __init__(self, cfg: SystemConfig, node_id: str, link: Receiver,
                 outputs: Sequence[Sender], *, beacon_sink: BeaconCounter | None = None,
                 route_filter: bool = True, logger: EventLogger | None = None):
        node = next((n for n in cfg.nodes if n.id == node_id), None)
        if node is None:
            raise KeyError(f"unknown node '{node_id}'")
        self.node_id, self.segment = node_id, node.segment
        self._pre = cfg.alert.preamble
        self._link, self._outputs = link, list(outputs)
        self._sink, self._filter, self._log = beacon_sink, route_filter, logger
        self.stats = {"rx": 0, "forwarded": 0, "off_route": 0, "beacon": 0,
                      "invalid": 0, "ignored": 0, "send_errors": 0}

    def step(self, timeout: float | None = 0.0) -> RelayOutcome | None:
        """Handle at most one datagram. Returns None only on timeout."""
        d = self._link.recv(timeout)
        if d is None:
            return None
        self.stats["rx"] += 1
        out = self._handle(d.data, d.rx_time)
        self.stats[out.reason] += 1
        if self._log is not None:
            self._log.log("relay", node=self.node_id, reason=out.reason,
                          delivered_to=out.delivered_to, bytes=len(d.data))
        return out

    def drain(self) -> list[RelayOutcome]:
        outs = []
        while (o := self.step(0.0)) is not None:
            outs.append(o)
        return outs

    def _handle(self, data: bytes, rx_time: float) -> RelayOutcome:
        if P.peek_type(data, self._pre) is None:
            return RelayOutcome("ignored")
        try:
            msg = P.decode(data, self._pre)
        except P.PacketError:
            return RelayOutcome("invalid")
        if isinstance(msg, P.Beacon):
            if self._sink is not None:
                self._sink.ingest(msg, rx_time, self.node_id)
            return RelayOutcome("beacon")
        if self._filter and self.segment not in msg.route:
            return RelayOutcome("off_route")
        ok = 0
        for out in self._outputs:
            try:
                out.send(data)
                ok += 1
            except (OSError, ValueError):
                self.stats["send_errors"] += 1
        return RelayOutcome("forwarded", ok)

"""Abstract datagram link used by the alert layer.

The alert modules only know these two interfaces. A backend decides what is behind them:
OpenISAC's UDP data plane (real radios or ChannelSimulator), or an in-process loopback.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Datagram:
    data: bytes
    src: tuple[str, int] | None = None          # sender address, if known
    rx_time: float = field(default_factory=time.monotonic)   # local receive time (s)


class Sender(ABC):
    max_bytes: int = 65507                      # largest datagram this link accepts

    @abstractmethod
    def send(self, data: bytes) -> int:
        """Send one datagram. Returns bytes sent. Raises ValueError if too large."""

    def _check_size(self, data: bytes) -> None:
        if not data:
            raise ValueError("cannot send an empty datagram")
        if len(data) > self.max_bytes:
            raise ValueError(f"datagram is {len(data)} B, link limit is {self.max_bytes} B")

    def close(self) -> None:
        """Release resources. Safe to call more than once."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Receiver(ABC):
    @abstractmethod
    def recv(self, timeout: float | None = None) -> Datagram | None:
        """Wait up to ``timeout`` seconds (None = forever) for one datagram.

        Returns None on timeout.
        """

    def close(self) -> None:
        """Release resources. Safe to call more than once."""

    def __enter__(self):
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

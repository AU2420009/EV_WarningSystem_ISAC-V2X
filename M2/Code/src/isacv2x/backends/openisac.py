"""OpenISAC data-plane backend: UDP in/out of the untouched BS and UE executables.

Same code path for real USRPs and for OpenISAC's ChannelSimulator, because both expose the
same UDP ports. Port names follow OpenISAC's own naming (role-neutral); which project role
(EV, RSU, receiver) runs BS or UE is decided by the caller.

    BS  payload IN   udp_input_port   (50000)  -> sent over the air as downlink
    UE  payload OUT  udp_output_port  (50001)  <- decoded downlink
    UE  payload IN   udp_input_port   (50002)  -> sent over the air as uplink
    BS  payload OUT  udp_output_port  (50003)  <- decoded uplink

Checked against the OpenISAC BS/UE YAML reference pages (see docs/CAVEATS.md). Things
this module deliberately does NOT do because they are undocumented:
  * the ZMQ control protocol on ports 9999/10001 (message format unknown), and
  * the sensing message layout on ports 8888/8889.
Only their addresses are exposed (``Endpoints``), plus ``require_zmq()`` for later use.

Operational notes: the BS/UE YAMLs must set ``uplink.enabled: true`` for payload to flow
UE->BS. Decoded payload is sent to ``udp_output_ip`` (BS default 127.0.0.1), so receivers
must bind that address.
"""
from __future__ import annotations

import importlib.util
import socket
import time
from dataclasses import dataclass

from isacv2x.backends.base import Datagram, Receiver, Sender
from isacv2x.common.config import OpenISACCfg

Addr = tuple[str, int]


@dataclass(frozen=True)
class Endpoints:
    """Where each OpenISAC data/control socket lives, built from system.yaml."""
    bs_payload_in: Addr
    bs_payload_out: Addr
    ue_payload_in: Addr
    ue_payload_out: Addr
    mono_sensing: str          # ZMQ PUB (BS)
    bi_sensing: str            # ZMQ PUB (UE)
    bs_control: str            # ZMQ ROUTER
    ue_control: str            # ZMQ ROUTER

    @classmethod
    def from_config(cls, c: OpenISACCfg) -> "Endpoints":
        return cls(
            bs_payload_in=(c.host, c.bs_udp_in),
            bs_payload_out=(c.host, c.bs_udp_out),
            ue_payload_in=(c.host, c.ue_udp_in),
            ue_payload_out=(c.host, c.ue_udp_out),
            mono_sensing=f"tcp://{c.host}:{c.zmq_monostatic}",
            bi_sensing=f"tcp://{c.host}:{c.zmq_bistatic}",
            bs_control=f"tcp://{c.host}:{c.zmq_bs_control}",
            ue_control=f"tcp://{c.host}:{c.zmq_ue_control}",
        )


class UdpSender(Sender):
    """Send datagrams to one fixed address (e.g. a BS or UE payload input port)."""

    def __init__(self, dest: Addr, *, max_bytes: int | None = None):
        self.dest = dest
        if max_bytes is not None:
            if not 1 <= max_bytes <= Sender.max_bytes:
                raise ValueError(f"max_bytes must be in 1..{Sender.max_bytes}")
            self.max_bytes = max_bytes
        self._sock: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def send(self, data: bytes) -> int:
        self._check_size(data)
        if self._sock is None:
            raise RuntimeError("sender is closed")
        return self._sock.sendto(data, self.dest)

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class UdpReceiver(Receiver):
    """Bind a UDP port and read datagrams (e.g. a BS/UE payload output port)."""

    def __init__(self, bind: Addr, *, buffer_bytes: int = 65535):
        self._buf = buffer_bytes
        self._sock: socket.socket | None = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.bind(bind)
        except OSError:
            self._sock.close()
            self._sock = None
            raise

    @property
    def address(self) -> Addr:
        """Actual bound address (useful when binding port 0)."""
        if self._sock is None:
            raise RuntimeError("receiver is closed")
        host, port = self._sock.getsockname()[:2]
        return host, port

    def recv(self, timeout: float | None = None) -> Datagram | None:
        if self._sock is None:
            raise RuntimeError("receiver is closed")
        self._sock.settimeout(timeout)
        try:
            data, src = self._sock.recvfrom(self._buf)
        except socket.timeout:
            return None
        return Datagram(data, (src[0], src[1]), time.monotonic())

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


# ------------------------------------------------------------------ helpers
def zmq_available() -> bool:
    return importlib.util.find_spec("zmq") is not None


def require_zmq():
    """Import and return pyzmq, or raise a clear error if it isn't installed."""
    if not zmq_available():
        raise ImportError("pyzmq is required for OpenISAC sensing/control sockets: "
                          "pip install pyzmq")
    import zmq
    return zmq

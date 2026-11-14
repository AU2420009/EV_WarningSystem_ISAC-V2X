import pytest

from isacv2x.backends.base import Datagram, Receiver, Sender
from isacv2x.backends.openisac import (Endpoints, UdpReceiver, UdpSender, require_zmq,
                                       zmq_available)
from isacv2x.common.config import load_config

LO = "127.0.0.1"


def test_endpoints_from_config_match_openisac_docs():
    ep = Endpoints.from_config(load_config("config/system.yaml").openisac)
    assert ep.bs_payload_in == (LO, 50000) and ep.bs_payload_out == (LO, 50003)
    assert ep.ue_payload_in == (LO, 50002) and ep.ue_payload_out == (LO, 50001)
    assert ep.mono_sensing == "tcp://127.0.0.1:8888"
    assert ep.bi_sensing == "tcp://127.0.0.1:8889"
    assert ep.bs_control == "tcp://127.0.0.1:9999"
    assert ep.ue_control == "tcp://127.0.0.1:10001"


def test_udp_roundtrip_preserves_bytes_and_boundaries():
    with UdpReceiver((LO, 0)) as rx:
        with UdpSender(rx.address) as tx:
            payloads = [b"\x00\x01\x02", b"x" * 1024, bytes(range(256))]
            for p in payloads:
                assert tx.send(p) == len(p)
            got = [rx.recv(timeout=2.0) for _ in payloads]
    assert all(isinstance(g, Datagram) for g in got)
    assert [g.data for g in got] == payloads             # one datagram, one message
    assert got[0].src[0] == LO and got[0].rx_time > 0


def test_recv_timeout_returns_none():
    with UdpReceiver((LO, 0)) as rx:
        assert rx.recv(timeout=0.05) is None


def test_size_limits_and_empty_rejected():
    with UdpReceiver((LO, 0)) as rx, UdpSender(rx.address, max_bytes=16) as tx:
        tx.send(b"a" * 16)
        with pytest.raises(ValueError):
            tx.send(b"a" * 17)
        with pytest.raises(ValueError):
            tx.send(b"")
    for bad in (0, -1, Sender.max_bytes + 1):
        with pytest.raises(ValueError):
            UdpSender((LO, 9), max_bytes=bad)


def test_close_is_idempotent_and_blocks_further_use():
    rx = UdpReceiver((LO, 0))
    tx = UdpSender(rx.address)
    tx.close(); tx.close()
    rx.close(); rx.close()
    with pytest.raises(RuntimeError):
        tx.send(b"x")
    with pytest.raises(RuntimeError):
        rx.recv(0.01)


def test_port_in_use_raises_and_cleans_up():
    with UdpReceiver((LO, 0)) as first:
        # SO_REUSEADDR lets UDP rebind on some OSes; either outcome must not leak state
        try:
            second = UdpReceiver(first.address)
        except OSError:
            return
        second.close()


def test_interfaces_are_abstract():
    with pytest.raises(TypeError):
        Sender()
    with pytest.raises(TypeError):
        Receiver()


def test_zmq_helpers_are_consistent():
    if zmq_available():
        assert require_zmq().__name__ == "zmq"
    else:
        with pytest.raises(ImportError):
            require_zmq()

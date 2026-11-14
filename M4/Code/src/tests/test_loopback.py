import time

import pytest

from isacv2x.backends.loopback import LoopbackChannel


def test_delivers_in_order_without_impairment():
    ch = LoopbackChannel()
    tx, rx = ch.sender(), ch.receiver()
    for i in range(5):
        tx.send(bytes([i]))
    assert [rx.recv(0.0).data for _ in range(5)] == [bytes([i]) for i in range(5)]
    assert rx.recv(0.0) is None
    assert ch.stats == {"sent": 5, "dropped": 0, "delivered": 5}


def test_loss_is_seeded_and_close_to_target():
    def run(seed):
        ch = LoopbackChannel(loss=0.3, seed=seed)
        tx, rx = ch.sender(), ch.receiver()
        for i in range(2000):
            tx.send(b"x")
        n = 0
        while rx.recv(0.0) is not None:
            n += 1
        return n, ch.stats["dropped"]
    a, b = run(5), run(5)
    assert a == b                                    # reproducible
    assert run(6) != a                               # different seed, different pattern
    assert 0.25 < a[1] / 2000 < 0.35


def test_total_loss_and_no_loss_extremes():
    dead = LoopbackChannel(loss=1.0)
    dead.sender().send(b"x")
    assert dead.receiver().recv(0.0) is None
    ok = LoopbackChannel(loss=0.0)
    ok.sender().send(b"x")
    assert ok.receiver().recv(0.0).data == b"x"


def test_delay_holds_datagram_until_due():
    ch = LoopbackChannel(delay_s=0.1)
    tx, rx = ch.sender(), ch.receiver()
    t0 = time.monotonic()
    tx.send(b"late")
    assert rx.recv(0.02) is None
    got = rx.recv(1.0)
    assert got.data == b"late" and time.monotonic() - t0 >= 0.09


def test_blocking_recv_wakes_on_send():
    import threading
    ch = LoopbackChannel()
    tx, rx = ch.sender(), ch.receiver()
    threading.Timer(0.05, tx.send, args=(b"hi",)).start()
    assert rx.recv(2.0).data == b"hi"


def test_validation_and_closed_state():
    for kw in ({"loss": -0.1}, {"loss": 1.1}, {"delay_s": -1}, {"jitter_s": -1},
               {"max_bytes": 0}):
        with pytest.raises(ValueError):
            LoopbackChannel(**kw)
    ch = LoopbackChannel(max_bytes=8)
    tx, rx = ch.sender(), ch.receiver()
    with pytest.raises(ValueError):
        tx.send(b"123456789")
    with pytest.raises(ValueError):
        tx.send(b"")
    tx.close(); rx.close()
    with pytest.raises(RuntimeError):
        tx.send(b"x")
    with pytest.raises(RuntimeError):
        rx.recv(0.0)


def test_sent_bytes_are_copied():
    ch = LoopbackChannel()
    buf = bytearray(b"abc")
    ch.sender().send(buf)
    buf[0] = 0
    assert ch.receiver().recv(0.0).data == b"abc"

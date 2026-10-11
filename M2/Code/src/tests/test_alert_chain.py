"""End to end on loopback: EV -> (lossy link) -> relay -> (lossy links) -> 3 receivers.

Shows why the transmitter repeats each alert, and gives an early PDR number for the
report's >= 90 % target on a simulated link (not radio measurements).
"""
import yaml

from isacv2x.alert.receiver import AlertReceiver
from isacv2x.alert.relay import Relay
from isacv2x.alert.transmitter import AlertTransmitter
from isacv2x.backends.loopback import LoopbackChannel
from isacv2x.common.config import load_config, parse_config

N = 1000
ROUTE = ("S12", "S23")                 # RLY1 and receivers sit on S23


def run(repeat, loss, seed=100):
    raw = yaml.safe_load(open("config/system.yaml"))
    raw["alert"]["repeat"] = repeat
    cfg = parse_config(raw)

    ev_link = LoopbackChannel(loss=loss, seed=seed)
    hop = [LoopbackChannel(loss=loss, seed=seed + 1 + i) for i in range(3)]
    tx = AlertTransmitter(cfg, ev_link.sender(), sleep=lambda s: None)
    relay = Relay(cfg, "RLY1", ev_link.receiver(), [h.sender() for h in hop])
    rxs = [AlertReceiver(cfg, h.receiver(), f"RX{i + 1}", segment="S23")
           for i, h in enumerate(hop)]

    got = [set() for _ in rxs]
    for _ in range(N):
        sent = tx.send_alert(ROUTE)
        relay.drain()
        for i, rx in enumerate(rxs):
            for o in rx.drain():
                if o.reason == "ok" and o.event.on_route:
                    got[i].add(o.event.alert.seq)
        assert sent.alert.seq in range(1, N + 1)
    return [len(g) / N for g in got]


def test_repeats_lift_pdr_above_target_on_a_lossy_chain():
    pdr = run(repeat=3, loss=0.10)
    assert all(p >= 0.90 for p in pdr), pdr


def test_single_copy_misses_target_at_same_loss():
    pdr = run(repeat=1, loss=0.10)             # expected ~0.81 (two lossy hops)
    assert all(p < 0.90 for p in pdr), pdr


def test_lossless_chain_delivers_everything():
    assert run(repeat=1, loss=0.0) == [1.0, 1.0, 1.0]


def test_run_is_reproducible():
    assert run(repeat=2, loss=0.2, seed=7) == run(repeat=2, loss=0.2, seed=7)

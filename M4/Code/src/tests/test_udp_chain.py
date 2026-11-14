"""Alert chain over real UDP sockets, with a tiny stand-in for OpenISAC's BS->UE data plane."""
import threading

from isacv2x.alert.receiver import AlertReceiver
from isacv2x.alert.transmitter import AlertTransmitter
from isacv2x.backends.openisac import UdpReceiver, UdpSender
from isacv2x.common.config import load_config

cfg = load_config("config/system.yaml")


def test_alert_goes_through_udp_stand_in_for_bs_and_ue():
    bs_in = UdpReceiver(("127.0.0.1", 0))              # "BS payload input"
    ue_out = UdpReceiver(("127.0.0.1", 0))             # "UE decoded output"
    fwd_out = UdpSender(ue_out.address)
    stop = threading.Event()

    def air():                                          # BS -> (channel) -> UE
        while not stop.is_set():
            d = bs_in.recv(0.05)
            if d is not None:
                fwd_out.send(d.data)

    th = threading.Thread(target=air, daemon=True)
    th.start()
    try:
        tx = AlertTransmitter(cfg, UdpSender(bs_in.address), sleep=lambda s: None)
        rx = AlertReceiver(cfg, ue_out, "RX1", segment="S23")
        tx.send_alert(("S12", "S23"))
        got = []
        for _ in range(cfg.alert.repeat):
            o = rx.poll(1.0)
            assert o is not None
            got.append(o.reason)
        assert got == ["ok", "duplicate", "duplicate"]
        assert rx.stats["ok"] == 1
    finally:
        stop.set()
        th.join(1)
        for x in (bs_in, ue_out, fwd_out):
            x.close()

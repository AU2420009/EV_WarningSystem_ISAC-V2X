#!/usr/bin/env python3
"""Hardware Bench Test Utility for USRP B210 with OpenISAC.

Enables testing over real SDR hardware across the 3 roles:
  1. Civilian (UE Uplink): Generates Poisson/periodic beacons -> UDP port 50002 -> USRP TX.
  2. RSU (BS Dual-Link): USRP RX -> UDP port 50003 -> BeaconCounter + A* -> RouteFeedback on 50000.
  3. EV / Civilian RX (UE Downlink): USRP RX -> UDP port 50001 -> AlertReceiver (decodes alerts).

Usage:
  python scripts/b210_bench_test.py --role civilian --rate 0.5   # 0.5 veh/s Poisson arrivals
  python scripts/b210_bench_test.py --role rsu                  # RSU density + routing hub
  python scripts/b210_bench_test.py --role receiver             # Civilian car receiving alerts
"""
from __future__ import annotations

import argparse
import socket
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from isacv2x.alert.receiver import AlertReceiver
from isacv2x.backends.openisac import UdpReceiver, UdpSender
from isacv2x.common import packets as P
from isacv2x.common.config import load_config
from isacv2x.density.beacon_counter import BeaconCounter
from isacv2x.density.poisson import PoissonTrafficGenerator
from isacv2x.routing.graph import RoadGraph
from isacv2x.routing.replanner import Replanner


def run_civilian(cfg_path: str, rate: float, segment: str):
    cfg = load_config(cfg_path)
    # OpenISAC UE uplink payload in: 50002
    dest_tx = (cfg.openisac.host, cfg.openisac.ue_udp_in)
    # OpenISAC UE downlink payload out: 50001
    bind_rx = (cfg.openisac.host, cfg.openisac.ue_udp_out)

    sender = UdpSender(dest_tx)
    rx = UdpReceiver(bind_rx)
    gen = PoissonTrafficGenerator(cfg, arrival_rates={segment: rate}, beacon_hz=2.0)
    print(f"[*] Civilian Vehicle (CV) running:")
    print(f"    -> Transmitting Poisson Beacons (rate={rate} veh/s on {segment}) to {dest_tx}")
    print(f"    <- Listening for Emergency Alerts on {bind_rx}")

    t0 = time.time()
    count = 0
    try:
        while True:
            now = time.time() - t0
            beacons = gen.step(now, dt=0.2)
            for b in beacons:
                raw = P.encode(b, cfg.alert.preamble, cfg.alert.max_payload_bytes)
                sender.send(raw)
                count += 1
                print(f"  -> TX Beacon: vid={b.vehicle_id} seq={b.seq} seg={b.segment} off={b.offset_m:.1f}m")

            # Check for incoming Emergency Alerts
            dg = rx.recv(timeout=0.05)
            if dg is not None:
                try:
                    msg = P.decode(dg.data, cfg.alert.preamble)
                    if isinstance(msg, P.Alert):
                        if segment in msg.route:
                            print(f"\n  [!!! URGENT WARNING !!!] EV #{msg.ev_id} APPROACHING ON YOUR SEGMENT {segment}!")
                            print(f"      Actuation: PULL OVER IMMEDIATELY. EV Priority: {msg.priority}\n")
                        else:
                            print(f"  [Notice]: EV #{msg.ev_id} active on route: {' -> '.join(msg.route)}")
                except P.PacketError as e:
                    print(f"  [!] Dropped corrupted packet: {e}")

            time.sleep(0.2)
    except KeyboardInterrupt:
        print(f"\n[+] Civilian Stopped. Sent {count} beacons.")
    finally:
        sender.close()
        rx.close()


def run_rsu(cfg_path: str):
    cfg = load_config(cfg_path)
    # OpenISAC BS uplink payload out: 50003
    bind_in = (cfg.openisac.host, cfg.openisac.bs_udp_out)
    # OpenISAC BS downlink payload in: 50000
    dest_out = (cfg.openisac.host, cfg.openisac.bs_udp_in)

    rx = UdpReceiver(bind_in)
    tx = UdpSender(dest_out)

    counter = BeaconCounter(cfg)
    graph = RoadGraph(cfg)
    planner = Replanner(graph, cfg.routing, "J1", "J6")

    print(f"[*] RSU Node running: Listening for USRP beacons on {bind_in}...")
    print(f"[*] Downlink feedback destination: {dest_out}")

    last_check = time.time()
    seq = 0
    try:
        while True:
            # Drain incoming packets
            while True:
                dg = rx.recv(timeout=0.005)
                if dg is None:
                    break
                status = counter.ingest_bytes(dg.data, dg.rx_time, node_id="RSU1")
                if status == "ok":
                    msg = P.decode(dg.data, cfg.alert.preamble)
                    print(f"  <- RX Beacon OK: vid={msg.vehicle_id} seg={msg.segment}")

            now = time.time()
            if now - last_check >= 1.0:
                last_check = now
                est = counter.estimate(now)
                res = planner.update_counts(est.counts)
                print(f"\n[Traffic Status @ {now:.1f}s]: Counts={dict(est.counts)}")
                if res.route:
                    print(f"  [Optimal Route]: {' -> '.join(res.route.segments)} (Cost={res.route.cost:.1f})")
                    if res.route_changed:
                        print("  [ALERT] Route changed! Broadcasting RouteFeedback over USRP downlink...")
                        seq += 1
                        fb = P.RouteFeedback("RSU1", seq, res.route.segments,
                                             congestion_level=min(1.0, max(est.counts.values()) / 20.0))
                        raw = P.encode(fb, cfg.alert.preamble, cfg.alert.max_payload_bytes)
                        tx.send(raw)

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[+] RSU Stopped.")
    finally:
        rx.close()
        tx.close()


def run_ev(cfg_path: str):
    cfg = load_config(cfg_path)
    # OpenISAC UE uplink payload in: 50002 (transmits alert to baseband)
    dest_tx = (cfg.openisac.host, cfg.openisac.ue_udp_in)
    # OpenISAC UE downlink payload out: 50001 (listens for RSU feedback)
    bind_rx = (cfg.openisac.host, cfg.openisac.ue_udp_out)

    tx = UdpSender(dest_tx)
    rx = UdpReceiver(bind_rx)
    print(f"[*] Emergency Vehicle (EV) Node running:")
    print(f"    -> Transmitting Siren Alerts to {dest_tx} (Uplink -> RSU)")
    print(f"    <- Listening for Route Feedback on {bind_rx} (Downlink <- RSU)")

    seq = 0
    planned_route = ["S12", "S23", "S34", "S45"]
    last_tx = 0.0
    try:
        while True:
            now = time.time()
            if now - last_tx >= 1.0:
                last_tx = now
                seq += 1
                alert = P.Alert(
                    ev_id=1,
                    priority=1,
                    seq=seq,
                    route=tuple(planned_route),
                )
                raw = P.encode(alert, cfg.alert.preamble, cfg.alert.max_payload_bytes)
                tx.send(raw)
                print(f"  -> TX Siren Alert #{seq}: EV #1 Priority 1 | Route: {' -> '.join(planned_route)}")

            dg = rx.recv(timeout=0.05)
            if dg is not None:
                try:
                    msg = P.decode(dg.data, cfg.alert.preamble)
                    if isinstance(msg, P.RouteFeedback):
                        print(f"\n  [RSU ROUTE FEEDBACK RECEIVED]: Congestion={msg.congestion_level:.2f}")
                        print(f"  [EV DYNAMIC REROUTE]: Updating route to: {' -> '.join(msg.route)}\n")
                        planned_route = list(msg.route)
                except P.PacketError as e:
                    print(f"  [!] Dropped corrupted packet: {e}")

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n[+] EV Node Stopped.")
    finally:
        tx.close()
        rx.close()


def run_receiver(cfg_path: str):
    cfg = load_config(cfg_path)
    # OpenISAC UE downlink payload out: 50001
    bind_in = (cfg.openisac.host, cfg.openisac.ue_udp_out)
    rx = UdpReceiver(bind_in)
    print(f"[*] Civilian Receiver running: Listening on {bind_in} for alerts/feedback...")

    try:
        while True:
            dg = rx.recv(timeout=0.05)
            if dg is not None:
                try:
                    msg = P.decode(dg.data, cfg.alert.preamble)
                    if isinstance(msg, P.Alert):
                        print(f"  [!!! EMERGENCY ALERT !!!]: EV #{msg.ev_id} Priority {msg.priority}")
                        print(f"      Clear road segments: {' -> '.join(msg.route)}")
                    elif isinstance(msg, P.RouteFeedback):
                        print(f"  [RSU Feedback]: Best Path = {' -> '.join(msg.route)} (Congestion={msg.congestion_level:.2f})")
                except P.PacketError as e:
                    print(f"  [!] Dropped corrupted packet: {e}")
            time.sleep(0.02)
    except KeyboardInterrupt:
        print("\n[+] Receiver Stopped.")
    finally:
        rx.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--role", choices=["civilian", "rsu", "receiver", "ev"], required=True)
    ap.add_argument("--config", default="config/system.yaml")
    ap.add_argument("--rate", type=float, default=0.5, help="Poisson arrival rate (veh/s)")
    ap.add_argument("--segment", default="S12", help="Road segment for civilian vehicle")
    args = ap.parse_args()

    if args.role == "ev":
        run_ev(args.config)
    elif args.role == "civilian":
        run_civilian(args.config, args.rate, args.segment)
    elif args.role == "rsu":
        run_rsu(args.config)
    elif args.role == "receiver":
        run_receiver(args.config)


if __name__ == "__main__":
    main()


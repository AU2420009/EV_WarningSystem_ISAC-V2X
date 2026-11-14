#!/usr/bin/env python3
"""EV intent -> sense -> route -> alert: simulated end-to-end run plus Table II checklist.

    python scripts/run_demo.py [--config config/system.yaml] [--seed N] [--out DIR]

Virtual-time end-to-end run: vehicles -> beacons -> density -> replan -> alert -> 3 nodes.

Beacons and density use a *virtual* clock (fast, deterministic); the alert chain runs on
in-process loopback links with real clocks, so alert latency is software time only.
Everything is written to one JSONL log that the eval modules read:

  truth (manual count), density, replan, alert_tx, alert_rx

Alerts are sent once per step with the current route. The hub relay forwards every alert
(route_filter=False) and receivers have no segment, so each is expected to get all alerts.

Default scenario: three phases that each flip the best route for J1 -> J6.
"""
from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from isacv2x.alert.receiver import AlertReceiver                       # noqa: E402
from isacv2x.alert.relay import Relay                                  # noqa: E402
from isacv2x.alert.transmitter import AlertTransmitter                # noqa: E402
from isacv2x.backends.loopback import LoopbackChannel                  # noqa: E402
from isacv2x.common import packets as P                                # noqa: E402
from isacv2x.common.config import SystemConfig, load_config            # noqa: E402
from isacv2x.common.logging_utils import EventLogger                   # noqa: E402
from isacv2x.density.base import feed                                  # noqa: E402
from isacv2x.density.beacon_counter import BeaconCounter               # noqa: E402
from isacv2x.density.factory import make_estimator                     # noqa: E402
from isacv2x.eval.report import build_report, to_markdown              # noqa: E402
from isacv2x.eval.signal_id import evaluate_signal_id                  # noqa: E402
from isacv2x.routing.graph import RoadGraph                            # noqa: E402
from isacv2x.routing.replanner import Replanner                        # noqa: E402


@dataclass(frozen=True)
class Phase:
    name: str
    duration_s: int
    counts: Mapping[str, int]            # manual vehicle count per segment


DEFAULT_PHASES = (
    Phase("light", 30, {"S12": 2, "S23": 2, "S25": 1, "S45": 4}),
    Phase("congested_S12", 30, {"S12": 18, "S23": 2, "S25": 1, "S45": 4}),
    Phase("congested_S23_S45", 30, {"S12": 2, "S23": 18, "S25": 1, "S45": 18}),
)


@dataclass(frozen=True)
class DemoParams:
    src: str = "J1"
    dst: str = "J6"
    phases: Sequence[Phase] = DEFAULT_PHASES
    beacon_hz: float = 2.0
    beacon_loss: float = 0.10
    link_loss: float = 0.10              # per hop of the alert chain
    equipped_fraction: float = 1.0       # share of vehicles that beacon (lab = 1.0)
    speed_mps: float = 10.0
    seed: int | None = None


@dataclass
class DemoResult:
    events: list[dict]
    node_segments: dict[str, str | None]
    routes: list[tuple[str, ...]] = field(default_factory=list)   # route after each step


def run_demo(cfg: SystemConfig, p: DemoParams = DemoParams(),
             log_path: str | Path | None = None) -> DemoResult:
    seed = cfg.seed if p.seed is None else p.seed
    rng = random.Random(seed)
    for v, n in (("beacon_loss", p.beacon_loss), ("link_loss", p.link_loss),
                 ("equipped_fraction", p.equipped_fraction)):
        if not 0.0 <= n <= 1.0:
            raise ValueError(f"{v} must be in [0, 1]")

    keyframes = []
    t = 0.0
    for ph in p.phases:                               # for method == synthetic
        keyframes += [(t, ph.counts), (t + ph.duration_s - 1, ph.counts)]
        t += ph.duration_s
    est = make_estimator(cfg, keyframes=keyframes)
    is_beacon = isinstance(est, BeaconCounter)
    is_radar = est.name == "radar_counter"
    if is_radar:
        import numpy as np
        from isacv2x.density.radar_counter import synth_frame
        np_rng = np.random.default_rng(seed + 7)
        speed_rng = random.Random(seed + 99)
        vspeed: dict[int, float] = {}

    graph = RoadGraph(cfg)
    log = EventLogger(log_path)
    planner = Replanner(graph, cfg.routing, p.src, p.dst, logger=log)

    ev_link = LoopbackChannel(loss=p.link_loss, seed=seed + 1)
    hops = [LoopbackChannel(loss=p.link_loss, seed=seed + 2 + i)
            for i in range(len(cfg.receivers))]
    tx = AlertTransmitter(cfg, ev_link.sender(), logger=log, sleep=lambda s: None)
    hub = Relay(cfg, cfg.nodes[0].id, ev_link.receiver(), [h.sender() for h in hops],
                route_filter=False)
    rxs = [AlertReceiver(cfg, h.receiver(), r.id, segment=None, logger=log)
           for h, r in zip(hops, cfg.receivers)]

    fleet: dict[int, tuple[str, float, bool]] = {}    # id -> (segment, offset, equipped)
    next_id, seq, vt, routes = 1, 0, 0.0, []
    dt = 1.0 / p.beacon_hz

    with log:
        for ph in p.phases:
            fleet.clear()
            for seg, n in ph.counts.items():
                for _ in range(n):
                    fleet[next_id] = (seg, rng.uniform(0, cfg.segments[seg].length_m),
                                      rng.random() < p.equipped_fraction)
                    if is_radar:                       # radar sees every vehicle, equipped or not
                        vspeed[next_id] = speed_rng.uniform(6.0, 14.0) * speed_rng.choice((-1, 1))
                    next_id += 1
            for _ in range(ph.duration_s):
                if is_beacon or is_radar:
                    for _ in range(int(round(p.beacon_hz))):
                        vt += dt
                        seq += 1
                        for vid, (seg, off, eq) in list(fleet.items()):
                            off = (off + p.speed_mps * dt) % cfg.segments[seg].length_m
                            fleet[vid] = (seg, off, eq)
                            if is_radar or not eq or rng.random() < p.beacon_loss:
                                continue
                            frame = P.encode(P.Beacon(vid, seq, seg, off, p.speed_mps,
                                                      int(vt * 1e6)),
                                             cfg.alert.preamble, cfg.alert.max_payload_bytes)
                            est.ingest_bytes(frame, vt, cfg.nodes[0].id)
                        if is_radar:
                            for node in cfg.nodes:
                                tg = [(o, vspeed[v], 18.0) for v, (sg, o, _) in fleet.items()
                                      if sg == node.segment]
                                est.ingest_frame(node.id, synth_frame(
                                    est.params, tg, rng=np_rng), vt)
                else:
                    vt += 1.0
                log.log("truth", scenario=ph.name, at=vt, counts=dict(ph.counts))
                _, res = feed(est, planner, vt, log)
                if res.route is not None:
                    tx.send_alert(res.route)
                    hub.drain()
                    for r in rxs:
                        r.drain()
                    routes.append(res.route.segments)
    return DemoResult(log.events(), {r.id: None for r in cfg.receivers}, routes)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/system.yaml")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--beacon-loss", type=float, default=0.10)
    ap.add_argument("--link-loss", type=float, default=0.10)
    ap.add_argument("--equipped-fraction", type=float, default=1.0)
    ap.add_argument("--out", default=None, help="log/report directory (default eval.log_dir)")
    a = ap.parse_args(argv)

    cfg = load_config(a.config)
    out = Path(a.out or cfg.eval.log_dir)
    out.mkdir(parents=True, exist_ok=True)
    params = DemoParams(seed=a.seed, beacon_loss=a.beacon_loss, link_loss=a.link_loss,
                        equipped_fraction=a.equipped_fraction)
    try:
        log_path = out / "demo.jsonl"
        log_path.unlink(missing_ok=True)                  # one run per log file
        res = run_demo(cfg, params, log_path)
    except (NotImplementedError, ImportError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    crit = build_report(res.events, cfg, res.node_segments, evaluate_signal_id(cfg),
                        trip=(params.src, params.dst))
    md = to_markdown(crit)
    (out / "report.md").write_text(md, encoding="utf-8")
    print(md)
    print(f"log: {log_path}\nreport: {out / 'report.md'}")
    return 0 if all(c.passed is not False for c in crit) else 1


if __name__ == "__main__":
    raise SystemExit(main())

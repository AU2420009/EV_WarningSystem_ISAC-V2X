# ISAC-V2X

Emergency-vehicle (EV) sensing and alerting system built on top of OpenISAC links: vehicles beacon, nodes estimate road-segment density, the EV route is recomputed (Dijkstra / A*) as congestion changes, and a "make way" alert is sent along the route to 3+ nodes.

## Layout

| Path | Purpose |
|---|---|
| `config/system.yaml` | single source of truth (graph, nodes, ports, thresholds) |
| `isacv2x/common` | config, packet formats (CRC32 framing), JSONL event log |
| `isacv2x/backends` | OpenISAC UDP data plane, in-process lossy loopback |
| `isacv2x/density` | beacon counter, synthetic generator, estimator factory, incomplete radar counter (optional, needs numpy) |
| `isacv2x/routing` | road graph, Dijkstra, A*, replanner with deadline check |
| `isacv2x/alert` | transmitter (repeats), relay fan-out, receiver (de-dup, outcomes) |
| `isacv2x/eval` | pdr, density_validation, replan, latency (+ time saved), signal_id, report (Table II) |
| `scripts/run_demo.py` | EV intent -> sense -> route -> alert on a virtual clock; prints the checklist |
| `config/{BS,UE}_Sim_Duplex.yaml` | OVERLAYS merged over OpenISAC's own files (documented keys only) |
| `scripts/run_sim_stack.sh` | launch ChannelSimulator + BS + UE with the overlays |
| `docs/CAVEATS.md` | what is and is not covered; read before quoting results |

## Run

    pip install -e .[dev]          # or: pip install pyyaml pytest
    python -m pytest -q
    python scripts/run_demo.py --out logs        # writes logs/demo.jsonl, logs/report.md

Useful flags: `--seed`, `--beacon-loss`, `--link-loss`, `--equipped-fraction`
(set below 1.0 to see the density check fail on unequipped traffic).
Exit code is 0 if no criterion fails, 1 if one does, 2 on an unsupported config.

## Evaluation modules (one log per run)

| Criterion | Module | Reads |
|---|---|---|
| PDR >= 90 % on 3+ nodes | `eval/pdr.py` | `alert_tx`, `alert_rx` |
| Density vs manual count | `eval/density_validation.py` | `truth`, `density` |
| Route recompute time, adaptation | `eval/replan.py` | `replan` |
| Alert latency | `eval/latency.py` | `alert_rx` |
| EV signal-ID FN/FP | `eval/signal_id.py` | labelled frames |
| End to end | `eval/report.py` | all |

## OpenISAC simulator stack (no radios)

    git clone https://github.com/zhouzhiwen2000/OpenISAC && cd OpenISAC
    mkdir -p build && cd build && cmake .. && make -j ChannelSimulator BS UE   # needs its deps
    cd ../.. && OPENISAC_DIR=~/OpenISAC scripts/run_sim_stack.sh --dry-run     # writes build/BS.yaml, UE.yaml
    OPENISAC_DIR=~/OpenISAC scripts/run_sim_stack.sh                           # ChannelSimulator, BS, UE

The overlays set 2.4 GHz TDD with uplink on, our UDP/ZMQ ports, and three moving scatterers
for the radar counter. A test checks that their ports and frequency match `system.yaml`.
This stack has not been run here (we could not build OpenISAC fully); the launch order and keys come
from the OpenISAC docs. Payload path: BS in 50000 -> UE out 50001; UE in 50002 -> BS out 50003.

## Moving to hardware
When we are given access to USRP B210 SDRs, we will:
Point `openisac:` and `nodes:` at the real BS/UE (verify ports and `uplink.enabled`, see CAVEATS section 2), replace the loopback channels with `backends.openisac.UdpSender` /
`UdpReceiver`, log the same events, and call `build_report`. We will use one log per run.

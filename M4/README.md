# EV Warning System: ISAC-V2X

Course project for **ECE310: Wireless Communications**, Ahmedabad University
(School of Engineering and Applied Science).

Team: Manas Pandya, Anant Narayan, Maitrey Dave, Aniruddha Wairagade, Datt Suthar.

## What it is

An emergency vehicle (EV) loses time in congested roads, and drivers ahead often notice it
too late. This project is a software system, designed to run on top of
[OpenISAC](https://github.com/zhouzhiwen2000/OpenISAC) software-defined-radio links, that
closes the loop between sensing, routing and alerting:

1. Vehicles broadcast short **beacons** (ID, road segment, offset, speed).
2. Roadside units (RSUs) turn them into a **vehicle count per road segment** (an optional
   radar-based counter is also implemented).
3. A congestion-weighted **Dijkstra / A\*** planner picks the fastest route for the EV and
   sends it back to the EV as a **RouteFeedback** message.
4. The EV broadcasts a **"make way" alert** carrying its route, repeated for reliability.
   Receivers can filter on their own road segment.

All messages are binary frames with a CRC-32. One config file (`system.yaml`) defines the
road graph, nodes, ports and thresholds.

## Status: what has and has not been shown

| Item | Status |
|---|---|
| Software pipeline (sense, route, alert) | Implemented and tested (165 unit tests) |
| Six KPIs (PDR, density error, replan time, alert latency, signal ID, modelled time saved) | Measured **in simulation only**: virtual clock, in-process loopback links, synthetic bit errors |
| Radar-based counter | Implemented, incomplete; run on synthetic frames only |
| Over-the-air operation on USRP B210 radios | **Not achieved.** The BS and UE could not hear each other, for no cause we could diagnose. No over-the-air results exist. |

Suspected but untested causes of the hardware failure: the SDRs being very close together, a
bug in OpenISAC (or in how we used it), or driver misconfiguration. We could not run further
experiments after the last lab day.

Read the KPI results as software results, not radio results. In particular:
- Latency is in-process software time, not RF latency.
- Density error is 0 only when every vehicle sends beacons (it fails at 50% equipage).
- The route-saving figure is a modelled path-cost difference, not measured travel time.
- Signal-ID meets its target at BER 1e-4 only; it fails at 1e-3 and 1e-2.

Details and caveats are in the final report and in `M3/Code/docs/CAVEATS.md`.

## Repository layout

| Path | Contents |
|---|---|
| `M1/` | Milestone 1 report (concept, literature, targets) and video link |
| `M2/` | Milestone 2 report, video link, code snapshot (`M2/Code`), hardware deployment guide, team debrief notes |
| `M3/Code/` | Milestone 3 code update: the `isacv2x` package, scripts, configs, tests, logs. See `M3/Code/README.md` for the module map |
| `M4` | Final submission; including a report, video and code for the final project update |


## Quick start

Requires Python 3.10 or later.

    cd M3/Code/src
    pip install -e .[dev]                  # or: pip install pyyaml pytest
    python -m pytest -q                    # unit tests
    python scripts/run_demo.py --out logs  # simulated end-to-end run; writes logs/demo.jsonl and logs/report.md

Useful demo flags: `--seed`, `--beacon-loss`, `--link-loss`, `--equipped-fraction`.
The exit code is 0 if no criterion fails, 1 if one does, 2 for an unsupported config.

The radar counter needs extra packages: `pip install -e .[radar]` (numpy, pyzmq).

## Hardware bench (not working)

`M3/Code/src/scripts/b210_bench_test.py` implements four roles (civilian vehicle, RSU,
emergency vehicle, civilian receiver) that exchange our packets over UDP with OpenISAC's BS
and UE programs. The configs `config/BS_Hardware_Duplex.yaml` and `UE_Hardware_Duplex.yaml`
are for USRP B210 radios. Known issue: both files currently set the same `serial`, so each
needs the serial of its own radio. The bench has never produced a working link, so treat
these files as a starting point for debugging, not a verified setup.

## Reports and videos

- Milestone reports: the PDFs in `M1/` and `M2/`; the M4 final evaluation report accompanies the submission.
- Demo videos: see `Video_Link.txt` in each milestone folder.

## References

- Z. Zhou, C. Zhang, X. Xu, and Y. Zeng, "OpenISAC: An open-source real-time experimentation platform for OFDM-ISAC," arXiv:2601.03535, 2026.
- OpenISAC code: <https://github.com/zhouzhiwen2000/OpenISAC> (BSD-2-Clause).

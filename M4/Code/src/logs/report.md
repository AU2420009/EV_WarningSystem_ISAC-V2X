| Criterion | Result | Value | Target | Note |
|---|---|---|---|---|
| Packet delivery ratio | PASS | 1.000 over 3 nodes, 90 alerts | >= 0.90 on >= 3 nodes | conservative (Wilson) test passes |
| Density proxy vs manual count | PASS | MAE 0.00 veh, 3 scenarios | MAE <= 1.0 and busiest segment right, >= 2 scenarios | light: MAE 0.00; congested_S12: MAE 0.00; congested_S23_S45: MAE 0.00 |
| Route recomputation time | PASS | max 0.03 ms, 10 searches, 2 route changes | <= 0.5 s and route adapts |  |
| End-to-end operation | PASS | 90 alerts, 2 route adaptations, 0 stale-route alerts, 3 receiving nodes | density -> replan -> alert -> >= min nodes |  |
| EV signal-ID false negative / positive | PASS | FN 0.0275 (55/2000), FP 0.0000 (0/2000) | FN <= 0.05, FP <= 0.01 (full decoder) | header-only: FN 0.0070, FP 0.0000 |
| Alert latency | PASS | p95 1.000 ms, max 1.006 ms, 270 samples | p95 <= 100 ms | software path only on loopback |
| Time saved vs static route (model) | PASS | 30.2 s (20.8 %) per trip over 90 alerts | adaptive route never costlier on average | 0 alerts where the static route was cheaper |
| Implementation feasibility | MANUAL | manual review | - | see docs/CAVEATS.md |

# Caveats

## 1. Beacon counting is cooperative; the radar estimator is the cross-check

`density/beacon_counter.py` is the report's method and the default. It has these limits:

1. **Only equipped vehicles are counted.** Cars, bikes, pedestrians or trucks without the
   beacon hardware are invisible, so it under-counts, and the bias differs per road. In the
   lab every test vehicle beacons, so a good lab result says nothing about real traffic.
   `python scripts/run_demo.py --equipped-fraction 0.5` makes the failure visible.
2. **No independent check.** A stuck, silent or spoofed transmitter cannot be detected from
   beacons alone.
3. **Speed is cooperative only**, from reported speed or differenced positions.

`density/radar_counter.py` (optional, `density.method: radar_counter`) counts moving
targets in OpenISAC range-Doppler maps and needs no cooperation. See section 4 for what it
can and cannot do. The EV's *own* range/velocity measurement (architecture note step 2) and
an ACK frame back to the EV are still not implemented; the Milestone 1 report does not ask
for them.

None of the Milestone 1 Table II criteria require radar, and routing and alerting do not
depend on which estimator produced the counts.

## 2. OpenISAC facts and their verification status

Checked against the OpenISAC BS and UE YAML reference pages:

- BS: payload in `network_output.udp_input_port` (50000); decoded uplink out to
  `udp_output_ip:udp_output_port` (127.0.0.1:50003); control ZMQ ROUTER 9999; monostatic
  sensing PUB 8888.
- UE: payload in `network_output.udp_input_port` (50002); decoded downlink out
  (`udp_output_port` 50001, IP falls back to `runtime.default_out_ip`); control 10001;
  bistatic sensing 8889.
- `uplink.enabled` and `uplink.arq_enabled` both default to `false`, so the BS and UE YAMLs
  must switch the uplink on. The page values are "typical", not guaranteed parser defaults.

Not documented anywhere that was reachable, so **unverified**:

- Datagram framing: how a UDP datagram maps onto OFDM frames (one-to-one, split or
  aggregated), and the maximum datagram size. The paper tested 1024-byte packets.
- The runtime control message protocol on ports 9999/10001.
- Whether one BS can serve more than one UE (OpenISAC appears to be a point-to-point link,
  so the "3+ receiving nodes" criterion is expected to rely on virtual relay fan-out).
- The sensing message binary layout.

Sim-stack overlays (`config/*_Sim_Duplex.yaml`, `scripts/run_sim_stack.sh`) contain only keys
from the BS/UE YAML reference and are merged over the upstream files, which were not
available here. Checked by tests: port/frequency agreement with `system.yaml`, merge logic,
and a dry run against a fake upstream. Not checked: that OpenISAC accepts the merged files,
and the `rf_sampling` change to 20 MHz (remove it from the overlay if the build objects).

## 3. What the evaluation numbers are, and are not

`scripts/run_demo.py` produces the Table II checklist from a simulation. Read it as a test
of the software, not as radio results.

- **Virtual time for sensing.** Beacons and density run on a virtual clock with seeded
  random loss. Alerts run over in-process loopback links with seeded loss.
- **Alert latency is software time only** (sub-millisecond on loopback). It says nothing
  about OFDM framing, USRP buffering or UDP through OpenISAC. Re-measure with real
  hardware and synchronised clocks; negative samples mean clock skew and fail the check.
- **Thresholds are placeholders.** The report gives no numeric limits for density error,
  latency or signal-ID error. `eval.density_mae_tol`, `alert_latency_p95_s`,
  `signal_id_max_fn` and `signal_id_max_fp` are defaults chosen here; agree real values.
- **Signal-ID uses a synthetic corpus** with bit errors at a chosen BER. It shows the CRC
  decoder rejects non-EV frames, and how many alerts channel errors cost. With
  `--equipped-fraction < 1` the density check fails, which is caveat 1 made visible.
- **Receivers have no segment** in the demo and the hub forwards every alert, so each
  receiver is expected to get every alert. Per-segment relays are covered by unit tests.
- **Implementation feasibility** is a manual criterion and is reported as MANUAL.
- **Sensing is optional.** Only the radar method uses OpenISAC sensing, and in the demo it
  runs on synthetic range-Doppler frames. The EV's own range/velocity and an ACK frame back
  to the EV are not implemented (section 1).

## 4. Radar counter: documented, assumed, untested

Source: OpenISAC docs (Multichannel Monostatic Sensing, BS/UE YAML reference).

**Documented and used:** range resolution c/(2B); f_D = k_f / (M T_slow); v = c f_D / (2 f_c);
MTI as a zero-Doppler notch; BS sensing keys `sensing.range_fft_size` (1024),
`doppler_fft_size` (100), `symbol_stride` (20), `on_wire_format: complex_float32`,
`output_mode: dense | compact_mask`; sensing PUB at `network_output.mono_sensing_port`
(8888). The ChannelSimulator `simulation.targets[]` (`range_m`, `velocity_mps`, `gain_db`,
`angle_deg`) can inject moving scatterers for a hardware-free test.

**Not documented, so not implemented:** the ZMQ message layout (the plotting viewer demands a
"PARM V9" header, whose fields are not described), socket topic, array dtype/shape per
message, `compact_mask` encoding, and the backend CFAR sidecar format. `RadarCounter.start()`
therefore requires a caller-supplied `decode_frame(bytes) -> 2-D power array`, and the live
subscriber has not been run against a real BS. Read `scripts/plot_sensing_fast.py` in the
OpenISAC repo to write that decoder.

**Assumed:** `doppler_bin_hz` (default 8 Hz, about 0.5 m/s at 2.4 GHz) and `range_bin_m`
(c/2B). Set them from the BS YAML in use. All detection tests use synthetic frames.

**Physical limits that settings cannot remove:**
- 7.5 m range resolution at 20 MHz. Vehicles within a bin or two at similar speed merge into
  one peak; synthetic tests under-count by about 0.8 at 18 vehicles on one 400 m segment.
- Vehicles slower than `min_speed_mps` (default 1 m/s) sit in the MTI notch, so a standstill
  queue reads as empty. This is the opposite failure to beacon counting and matters exactly
  when congestion is worst. Use both estimators, or lower the gate and accept clutter.
- One radar covers one segment (the node's), looking along the road.

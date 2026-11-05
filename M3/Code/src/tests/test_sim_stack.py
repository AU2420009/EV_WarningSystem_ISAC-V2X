import os
import subprocess
import sys
from pathlib import Path

import yaml

from isacv2x.common.config import load_config

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import apply_overlay                                                  # noqa: E402

cfg = load_config(ROOT / "config/system.yaml")
BS = yaml.safe_load((ROOT / "config/BS_Sim_Duplex.yaml").read_text())
UE = yaml.safe_load((ROOT / "config/UE_Sim_Duplex.yaml").read_text())


def test_overlay_ports_match_system_yaml():
    o = cfg.openisac
    assert BS["network_output"]["udp_input_port"] == o.bs_udp_in
    assert BS["network_output"]["udp_output_port"] == o.bs_udp_out
    assert BS["network_output"]["mono_sensing_port"] == o.zmq_monostatic
    assert BS["network_output"]["control_port"] == o.zmq_bs_control
    assert UE["network_output"]["udp_input_port"] == o.ue_udp_in
    assert UE["network_output"]["udp_output_port"] == o.ue_udp_out
    assert UE["network_output"]["bi_sensing_port"] == o.zmq_bistatic
    assert UE["network_output"]["control_port"] == o.zmq_ue_control


def test_overlay_radio_settings_match_system_yaml_and_each_other():
    assert BS["downlink"]["center_freq"] == UE["downlink"]["center_freq"] == cfg.radio.center_freq_hz
    assert BS["rf_sampling"]["sample_rate"] == cfg.radio.sample_rate_hz
    for k in ("enabled", "duplex_mode", "symbol_start", "symbol_count", "guard_symbols"):
        assert BS["uplink"][k] == UE["uplink"][k]
    assert BS["simulation"]["session"] == UE["simulation"]["session"]
    assert BS["uplink"]["enabled"] is True and BS["simulation"]["enable_uplink"] is True


def test_overlay_targets_are_within_a_segment_and_use_documented_fields():
    longest = max(s.length_m for s in cfg.segments.values())
    for t in BS["simulation"]["targets"]:
        assert set(t) == {"range_m", "velocity_mps", "gain_db", "angle_deg"}
        assert 0 < t["range_m"] <= longest


def test_merge_keeps_upstream_keys_and_replaces_lists():
    base = {"a": {"x": 1, "y": 2}, "l": [1, 2], "keep": True}
    over = {"a": {"y": 9, "z": 3}, "l": [7]}
    assert apply_overlay.merge(base, over) == {"a": {"x": 1, "y": 9, "z": 3}, "l": [7],
                                               "keep": True}


def test_run_sim_stack_dry_run_with_fake_upstream(tmp_path):
    up = tmp_path / "OpenISAC"
    (up / "config").mkdir(parents=True)
    base = {"radio": {"radio_backend": "uhd"}, "ofdm_frame": {"fft_size": 1024},
            "network_output": {"udp_input_port": 1, "udp_output_port": 2, "extra": "kept"}}
    for f in ("BS_Sim_Duplex.yaml", "UE_Sim_Duplex.yaml"):
        (up / "config" / f).write_text(yaml.safe_dump(base))
    env = dict(os.environ, OPENISAC_DIR=str(up), PYTHON=sys.executable)
    r = subprocess.run(["bash", str(ROOT / "scripts/run_sim_stack.sh"), "--dry-run"],
                       env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    bs = yaml.safe_load((up / "build/BS.yaml").read_text())
    assert bs["radio"]["radio_backend"] == "sim"                      # overlay wins
    assert bs["ofdm_frame"]["fft_size"] == 1024                       # upstream kept
    assert bs["network_output"]["extra"] == "kept"
    assert bs["network_output"]["udp_input_port"] == cfg.openisac.bs_udp_in
    assert "dry run" in r.stdout


def test_run_sim_stack_needs_openisac_dir_and_binaries(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "OPENISAC_DIR"}
    r = subprocess.run(["bash", str(ROOT / "scripts/run_sim_stack.sh")], env=env,
                       capture_output=True, text=True)
    assert r.returncode != 0 and "OPENISAC_DIR" in r.stderr
    up = tmp_path / "o"
    (up / "config").mkdir(parents=True)
    for f in ("BS_Sim_Duplex.yaml", "UE_Sim_Duplex.yaml"):
        (up / "config" / f).write_text("{}")
    r = subprocess.run(["bash", str(ROOT / "scripts/run_sim_stack.sh")],
                       env=dict(os.environ, OPENISAC_DIR=str(up), PYTHON=sys.executable),
                       capture_output=True, text=True)
    assert r.returncode != 0 and "build OpenISAC first" in r.stderr

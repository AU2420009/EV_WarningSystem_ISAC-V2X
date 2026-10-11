import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from isacv2x.common.config import load_config, parse_config
from isacv2x.density.beacon_counter import BeaconCounter
from isacv2x.density.factory import make_estimator
from isacv2x.density.synthetic import SyntheticDensity
from isacv2x.eval.report import build_report, check_end_to_end, to_markdown
from isacv2x.eval.signal_id import evaluate_signal_id
from run_demo import DemoParams, run_demo
import run_demo as cli


def cfg_with(method):
    raw = yaml.safe_load(open("config/system.yaml"))
    raw["density"]["method"] = method
    return parse_config(raw)


def by_name(crit):
    return {c.name: c for c in crit}


def test_factory_methods():
    assert isinstance(make_estimator(cfg_with("beacon_counter")), BeaconCounter)
    assert isinstance(make_estimator(cfg_with("synthetic"), keyframes=[(0, {"S12": 1})]),
                      SyntheticDensity)
    with pytest.raises(ValueError):
        make_estimator(cfg_with("synthetic"))
    pytest.importorskip("numpy")
    assert make_estimator(cfg_with("radar_counter")).name == "radar_counter"



def test_default_demo_passes_every_automatic_criterion():
    cfg = load_config("config/system.yaml")
    res = run_demo(cfg)
    crit = build_report(res.events, cfg, res.node_segments, evaluate_signal_id(cfg))
    assert [c.name for c in crit if c.passed is False] == []
    assert by_name(crit)["Implementation feasibility"].passed is None
    assert len(set(res.routes)) == 2 or len(set(res.routes)) == 3
    assert "PASS" in to_markdown(crit)


def test_demo_is_reproducible():
    cfg = load_config("config/system.yaml")
    a, b = run_demo(cfg), run_demo(cfg)
    assert a.routes == b.routes
    pick = lambda r: [e["counts"] for e in r.events if e["event"] == "density"]
    assert pick(a) == pick(b)


def test_unequipped_traffic_breaks_density_validation():
    cfg = load_config("config/system.yaml")
    res = run_demo(cfg, DemoParams(equipped_fraction=0.5))
    crit = by_name(build_report(res.events, cfg, res.node_segments))
    assert crit["Density proxy vs manual count"].passed is False


def test_heavy_link_loss_fails_pdr():
    cfg = load_config("config/system.yaml")
    res = run_demo(cfg, DemoParams(link_loss=0.6))
    assert by_name(build_report(res.events, cfg, res.node_segments))[
        "Packet delivery ratio"].passed is False


def test_synthetic_method_runs_without_beacons():
    cfg = cfg_with("synthetic")
    res = run_demo(cfg)
    crit = by_name(build_report(res.events, cfg, res.node_segments))
    assert crit["End-to-end operation"].passed


def test_end_to_end_detects_stale_route_and_missing_nodes():
    ev = [{"event": "density"}, {"event": "replan", "replanned": True, "route": ["S12"]},
          {"event": "alert_tx", "copy": 1, "route": ["S99"]}]
    ok, note = check_end_to_end(ev, 1)
    assert not ok and "1 stale-route" in note


def test_validation_of_params():
    with pytest.raises(ValueError):
        run_demo(load_config("config/system.yaml"), DemoParams(link_loss=1.5))


def test_cli_writes_log_and_report(tmp_path):
    assert cli.main(["--out", str(tmp_path)]) == 0
    assert (tmp_path / "demo.jsonl").stat().st_size > 0
    assert "Packet delivery ratio" in (tmp_path / "report.md").read_text()


def test_radar_method_demo_counts_unequipped_traffic():
    pytest.importorskip("numpy")
    cfg = cfg_with("radar_counter")
    res = run_demo(cfg, DemoParams(equipped_fraction=0.0))     # no vehicle beacons at all
    crit = by_name(build_report(res.events, cfg, res.node_segments))
    assert crit["Density proxy vs manual count"].passed, crit["Density proxy vs manual count"]
    assert crit["End-to-end operation"].passed
    # the beacon counter on the same traffic sees nothing
    base = run_demo(load_config("config/system.yaml"), DemoParams(equipped_fraction=0.0))
    assert not by_name(build_report(base.events, cfg, base.node_segments))[
        "Density proxy vs manual count"].passed


def test_cli_reports_missing_dependency(tmp_path, monkeypatch, capsys):
    import isacv2x.density.factory as f
    monkeypatch.setattr(f, "make_estimator", lambda *a, **k: (_ for _ in ()).throw(
        ImportError("needs numpy")))
    monkeypatch.setattr(cli, "run_demo", lambda *a, **k: (_ for _ in ()).throw(
        ImportError("needs numpy")))
    assert cli.main(["--out", str(tmp_path)]) == 2
    assert "numpy" in capsys.readouterr().err

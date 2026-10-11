"""Load and validate config/system.yaml into typed dataclasses.

Usage:
    from isacv2x.common.config import load_config
    cfg = load_config("config/system.yaml")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    """Raised when system.yaml is missing fields or internally inconsistent."""


# ---------------------------------------------------------------- dataclasses
@dataclass(frozen=True)
class RadioCfg:
    center_freq_hz: float
    duplex: str
    sample_rate_hz: float


@dataclass(frozen=True)
class OpenISACCfg:
    host: str
    bs_udp_in: int
    ue_udp_out: int
    ue_udp_in: int
    bs_udp_out: int
    zmq_monostatic: int
    zmq_bistatic: int
    zmq_bs_control: int
    zmq_ue_control: int


@dataclass(frozen=True)
class Junction:
    id: str
    x: float
    y: float


@dataclass(frozen=True)
class Segment:
    id: str
    src: str
    dst: str
    length_m: float
    capacity_veh: float


@dataclass(frozen=True)
class Node:
    id: str
    role: str          # rsu | relay
    segment: str
    backend: str       # openisac | virtual
    host: str
    port: int


@dataclass(frozen=True)
class Receiver:
    id: str
    host: str
    port: int


@dataclass(frozen=True)
class DensityCfg:
    method: str
    interval_s: float
    beacon_ttl_s: float
    smoothing_alpha: float


@dataclass(frozen=True)
class RoutingCfg:
    algorithm: str
    congestion_weight: float
    congestion_exponent: float
    replan_min_change: float
    replan_deadline_s: float


@dataclass(frozen=True)
class AlertCfg:
    preamble: bytes
    ev_id: int
    priority: int
    repeat: int
    repeat_gap_s: float
    max_payload_bytes: int


@dataclass(frozen=True)
class EvalCfg:
    pdr_target: float
    min_receiving_nodes: int
    min_density_scenarios: int
    log_dir: str
    # Optional pass thresholds. The report gives no numbers for these, so they are
    # placeholders to be agreed with the team; override them under `eval:` in system.yaml.
    density_mae_tol: float = 1.0          # mean |estimate - truth|, vehicles per segment
    alert_latency_p95_s: float = 0.100    # 95th percentile alert latency, seconds
    signal_id_max_fn: float = 0.05        # EV alerts missed by the identifier
    signal_id_max_fp: float = 0.01        # non-EV frames accepted as EV alerts


@dataclass(frozen=True)
class SystemConfig:
    seed: int
    radio: RadioCfg
    openisac: OpenISACCfg
    junctions: dict[str, Junction]
    segments: dict[str, Segment]
    bidirectional: bool
    nodes: list[Node]
    receivers: list[Receiver]
    density: DensityCfg
    routing: RoutingCfg
    alert: AlertCfg
    eval: EvalCfg
    source_path: Path = field(default=Path("."), compare=False)

    def nodes_on(self, segment_id: str) -> list[Node]:
        return [n for n in self.nodes if n.segment == segment_id]


# ------------------------------------------------------------------- helpers
def _req(d: dict, key: str, where: str) -> Any:
    if not isinstance(d, dict) or key not in d:
        raise ConfigError(f"missing '{key}' in {where}")
    return d[key]


def _num(d: dict, key: str, where: str, *, lo: float | None = None,
         hi: float | None = None, positive: bool = False) -> float:
    v = _req(d, key, where)
    if isinstance(v, str):
        # PyYAML (YAML 1.1) parses '2.4e9' as a string; accept numeric strings.
        try:
            v = float(v)
        except ValueError:
            raise ConfigError(f"'{where}.{key}' must be a number, got {v!r}") from None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ConfigError(f"'{where}.{key}' must be a number, got {v!r}")
    if positive and v <= 0:
        raise ConfigError(f"'{where}.{key}' must be > 0, got {v}")
    if lo is not None and v < lo:
        raise ConfigError(f"'{where}.{key}' must be >= {lo}, got {v}")
    if hi is not None and v > hi:
        raise ConfigError(f"'{where}.{key}' must be <= {hi}, got {v}")
    return float(v)


def _opt(d: dict, key: str, default: float, where: str, **kw) -> float:
    """Like _num but returns ``default`` when the key is absent."""
    return _num(d, key, where, **kw) if key in d else default


def _int(d: dict, key: str, where: str, *, lo: int | None = None,
         hi: int | None = None) -> int:
    v = _req(d, key, where)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError(f"'{where}.{key}' must be an integer, got {v!r}")
    if lo is not None and v < lo:
        raise ConfigError(f"'{where}.{key}' must be >= {lo}, got {v}")
    if hi is not None and v > hi:
        raise ConfigError(f"'{where}.{key}' must be <= {hi}, got {v}")
    return v


def _choice(d: dict, key: str, where: str, options: tuple[str, ...]) -> str:
    v = _req(d, key, where)
    if v not in options:
        raise ConfigError(f"'{where}.{key}' must be one of {options}, got {v!r}")
    return v


def _port(d: dict, key: str, where: str) -> int:
    return _int(d, key, where, lo=1, hi=65535)


def _unique(ids: list[str], what: str) -> None:
    seen: set[str] = set()
    for i in ids:
        if i in seen:
            raise ConfigError(f"duplicate {what} id '{i}'")
        seen.add(i)


# -------------------------------------------------------------------- parsing
def _parse_openisac(d: dict) -> OpenISACCfg:
    w = "openisac"
    cfg = OpenISACCfg(
        host=str(_req(d, "host", w)),
        bs_udp_in=_port(d, "bs_udp_in", w),
        ue_udp_out=_port(d, "ue_udp_out", w),
        ue_udp_in=_port(d, "ue_udp_in", w),
        bs_udp_out=_port(d, "bs_udp_out", w),
        zmq_monostatic=_port(d, "zmq_monostatic", w),
        zmq_bistatic=_port(d, "zmq_bistatic", w),
        zmq_bs_control=_port(d, "zmq_bs_control", w),
        zmq_ue_control=_port(d, "zmq_ue_control", w),
    )
    ports = [v for k, v in cfg.__dict__.items() if k != "host"]
    if len(set(ports)) != len(ports):
        raise ConfigError("openisac: port numbers must all be distinct")
    return cfg


def _parse_graph(raw: dict) -> tuple[dict[str, Junction], dict[str, Segment]]:
    jraw = _req(raw, "junctions", "root")
    if not isinstance(jraw, dict) or not jraw:
        raise ConfigError("'junctions' must be a non-empty mapping")
    junctions = {
        jid: Junction(jid,
                      _num(j, "x", f"junctions.{jid}"),
                      _num(j, "y", f"junctions.{jid}"))
        for jid, j in jraw.items()
    }

    sraw = _req(raw, "segments", "root")
    if not isinstance(sraw, list) or not sraw:
        raise ConfigError("'segments' must be a non-empty list")
    _unique([s.get("id", "") for s in sraw], "segment")
    segments: dict[str, Segment] = {}
    for s in sraw:
        sid = _req(s, "id", "segments[]")
        w = f"segments.{sid}"
        src, dst = _req(s, "from", w), _req(s, "to", w)
        for end in (src, dst):
            if end not in junctions:
                raise ConfigError(f"{w}: unknown junction '{end}'")
        if src == dst:
            raise ConfigError(f"{w}: self-loop on '{src}'")
        segments[sid] = Segment(
            sid, src, dst,
            _num(s, "length_m", w, positive=True),
            _num(s, "capacity_veh", w, positive=True),
        )
    return junctions, segments


def _parse_nodes(raw: dict, segments: dict[str, Segment]) -> list[Node]:
    nraw = _req(raw, "nodes", "root")
    if not isinstance(nraw, list) or not nraw:
        raise ConfigError("'nodes' must be a non-empty list")
    _unique([n.get("id", "") for n in nraw], "node")
    nodes = []
    for n in nraw:
        nid = _req(n, "id", "nodes[]")
        w = f"nodes.{nid}"
        seg = _req(n, "segment", w)
        if seg not in segments:
            raise ConfigError(f"{w}: unknown segment '{seg}'")
        nodes.append(Node(
            nid,
            _choice(n, "role", w, ("rsu", "relay")),
            seg,
            _choice(n, "backend", w, ("openisac", "virtual")),
            str(_req(n, "host", w)),
            _port(n, "port", w),
        ))
    return nodes


def _parse_receivers(raw: dict) -> list[Receiver]:
    rraw = _req(raw, "receivers", "root")
    if not isinstance(rraw, list) or not rraw:
        raise ConfigError("'receivers' must be a non-empty list")
    _unique([r.get("id", "") for r in rraw], "receiver")
    return [
        Receiver(str(r["id"]), str(_req(r, "host", f"receivers.{r['id']}")),
                 _port(r, "port", f"receivers.{r['id']}"))
        for r in rraw
    ]


def _parse_alert(d: dict) -> AlertCfg:
    w = "alert"
    hexstr = str(_req(d, "preamble_hex", w))
    try:
        preamble = bytes.fromhex(hexstr)
    except ValueError as e:
        raise ConfigError(f"alert.preamble_hex is not valid hex: {hexstr!r}") from e
    if not 2 <= len(preamble) <= 8:
        raise ConfigError("alert.preamble_hex must encode 2-8 bytes")
    return AlertCfg(
        preamble=preamble,
        ev_id=_int(d, "ev_id", w, lo=0, hi=65535),
        priority=_int(d, "priority", w, lo=0, hi=255),
        repeat=_int(d, "repeat", w, lo=1),
        repeat_gap_s=_num(d, "repeat_gap_s", w, lo=0),
        max_payload_bytes=_int(d, "max_payload_bytes", w, lo=16),
    )


def parse_config(raw: dict, source_path: Path = Path(".")) -> SystemConfig:
    """Validate an already-loaded YAML mapping and build a SystemConfig."""
    if not isinstance(raw, dict):
        raise ConfigError("top-level YAML must be a mapping")

    radio_d = _req(raw, "radio", "root")
    radio = RadioCfg(
        _num(radio_d, "center_freq_hz", "radio", positive=True),
        _choice(radio_d, "duplex", "radio", ("TDD", "FDD")),
        _num(radio_d, "sample_rate_hz", "radio", positive=True),
    )

    junctions, segments = _parse_graph(raw)
    nodes = _parse_nodes(raw, segments)
    receivers = _parse_receivers(raw)

    # Every segment may be sensed by zero or more nodes, but at least one must be.
    if not {n.segment for n in nodes}:
        raise ConfigError("no segment has a sensing node")

    # Node and receiver ports must not collide on the same host.
    endpoints = [(n.host, n.port) for n in nodes] + [(r.host, r.port) for r in receivers]
    if len(set(endpoints)) != len(endpoints):
        raise ConfigError("two nodes/receivers share the same host:port")

    dd = _req(raw, "density", "root")
    density = DensityCfg(
        _choice(dd, "method", "density", ("beacon_counter", "radar_counter", "synthetic")),
        _num(dd, "interval_s", "density", positive=True),
        _num(dd, "beacon_ttl_s", "density", positive=True),
        _num(dd, "smoothing_alpha", "density", lo=0.0, hi=1.0, positive=True),
    )

    rd = _req(raw, "routing", "root")
    routing = RoutingCfg(
        _choice(rd, "algorithm", "routing", ("dijkstra", "astar")),
        _num(rd, "congestion_weight", "routing", lo=0.0),
        _num(rd, "congestion_exponent", "routing", positive=True),
        _num(rd, "replan_min_change", "routing", lo=0.0, hi=1.0),
        _num(rd, "replan_deadline_s", "routing", positive=True),
    )

    ed = _req(raw, "eval", "root")
    ev = EvalCfg(
        _num(ed, "pdr_target", "eval", lo=0.0, hi=1.0, positive=True),
        _int(ed, "min_receiving_nodes", "eval", lo=1),
        _int(ed, "min_density_scenarios", "eval", lo=1),
        str(_req(ed, "log_dir", "eval")),
        density_mae_tol=_opt(ed, "density_mae_tol", 1.0, "eval", lo=0.0),
        alert_latency_p95_s=_opt(ed, "alert_latency_p95_s", 0.100, "eval", positive=True),
        signal_id_max_fn=_opt(ed, "signal_id_max_fn", 0.05, "eval", lo=0.0, hi=1.0),
        signal_id_max_fp=_opt(ed, "signal_id_max_fp", 0.01, "eval", lo=0.0, hi=1.0),
    )
    if len(receivers) < ev.min_receiving_nodes:
        raise ConfigError(
            f"{len(receivers)} receivers configured but eval.min_receiving_nodes="
            f"{ev.min_receiving_nodes}")

    proj = raw.get("project", {})
    return SystemConfig(
        seed=int(proj.get("seed", 0)),
        radio=radio,
        openisac=_parse_openisac(_req(raw, "openisac", "root")),
        junctions=junctions,
        segments=segments,
        bidirectional=bool(raw.get("bidirectional", True)),
        nodes=nodes,
        receivers=receivers,
        density=density,
        routing=routing,
        alert=_parse_alert(_req(raw, "alert", "root")),
        eval=ev,
        source_path=source_path,
    )


def load_config(path: str | Path = "config/system.yaml") -> SystemConfig:
    p = Path(path)
    if not p.is_file():
        raise ConfigError(f"config file not found: {p}")
    with p.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return parse_config(raw, p)

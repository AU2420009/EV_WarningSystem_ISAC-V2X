import csv
import math
import tempfile
from pathlib import Path

import pytest

from isacv2x.common.config import Segment
from isacv2x.common.logging_utils import EventLogger, read_jsonl, write_csv


def test_in_memory_logging_and_filter():
    log = EventLogger(clock=lambda: 100.0, mono=lambda: 5.0)
    log.log("a", x=1)
    log.log("b", y=2)
    assert [e["event"] for e in log.events()] == ["a", "b"]
    assert log.events("a")[0] == {"t": 100.0, "mono": 5.0, "event": "a", "x": 1}


def test_reserved_field_rejected():
    log = EventLogger()
    with pytest.raises(ValueError):
        log.log("a", t=1)


def test_jsonable_conversions():
    log = EventLogger()
    rec = log.log("a", route=("S12", "S23"), nan=math.nan, raw=b"\x01\xff",
                  seg=Segment("S1", "J1", "J2", 10.0, 5.0), s={3})
    assert rec["route"] == ["S12", "S23"]
    assert rec["nan"] is None
    assert rec["raw"] == "01ff"
    assert rec["seg"]["length_m"] == 10.0
    assert rec["s"] == [3]


def test_file_roundtrip_and_csv():
    d = Path(tempfile.mkdtemp())
    p = d / "sub" / "run.jsonl"
    with EventLogger(p) as log:
        log.log("replan", compute_s=0.01, route=["S12"])
        log.log("alert", rx="RX1")
        log.log("replan", compute_s=0.02, route=["S14", "S45"])
    rows = read_jsonl(p, "replan")
    assert [r["compute_s"] for r in rows] == [0.01, 0.02]
    assert len(read_jsonl(p)) == 3
    n = write_csv(d / "out.csv", rows)
    assert n == 2
    with (d / "out.csv").open() as f:
        got = list(csv.DictReader(f))
    assert got[1]["route"] == '["S14", "S45"]'


def test_append_mode_keeps_previous_runs():
    d = Path(tempfile.mkdtemp())
    p = d / "r.jsonl"
    with EventLogger(p) as a:
        a.log("one")
    with EventLogger(p) as b:
        b.log("two")
    assert [r["event"] for r in read_jsonl(p)] == ["one", "two"]

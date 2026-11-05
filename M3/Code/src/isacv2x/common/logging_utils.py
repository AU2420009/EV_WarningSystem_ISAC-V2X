"""Structured event logging for the evaluation modules.

Every event is one JSON object per line (JSONL) with a wall-clock time ``t`` (epoch s),
a monotonic time ``mono`` (for latency maths) and an ``event`` name plus free fields.
JSONL is easy to append from several modules and to load into pandas/CSV later.

    log = EventLogger("logs/run1.jsonl")      # or EventLogger() for in-memory only
    log.log("replan", compute_s=0.003, route=["S12", "S23"])
    events = log.events("replan")
"""
from __future__ import annotations

import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable


def _jsonable(v: Any) -> Any:
    """Make common values JSON-safe (tuples, sets, bytes, NaN/inf, dataclass-like)."""
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, (tuple, list, set, frozenset)):
        return [_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _jsonable(x) for k, x in v.items()}
    if isinstance(v, (bytes, bytearray)):
        return bytes(v).hex()
    if hasattr(v, "__dataclass_fields__"):
        return {k: _jsonable(getattr(v, k)) for k in v.__dataclass_fields__}
    return v


class EventLogger:
    """Thread-safe JSONL logger that also keeps events in memory for quick analysis."""

    def __init__(self, path: str | Path | None = None, *,
                 clock: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic,
                 keep_in_memory: bool = True):
        self._path = Path(path) if path is not None else None
        self._clock, self._mono = clock, mono
        self._keep = keep_in_memory
        self._events: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self._fh = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._fh = self._path.open("a", encoding="utf-8")

    def log(self, event: str, **fields: Any) -> dict[str, Any]:
        for reserved in ("t", "mono", "event"):
            if reserved in fields:
                raise ValueError(f"field name '{reserved}' is reserved")
        rec = {"t": self._clock(), "mono": self._mono(), "event": event,
               **{k: _jsonable(v) for k, v in fields.items()}}
        with self._lock:
            if self._keep:
                self._events.append(rec)
            if self._fh is not None:
                self._fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
                self._fh.flush()
        return rec

    def events(self, name: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            return [e for e in self._events if name is None or e["event"] == name]

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def __enter__(self) -> "EventLogger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_jsonl(path: str | Path, event: str | None = None) -> list[dict[str, Any]]:
    """Load a JSONL log written by EventLogger, optionally filtering by event name."""
    out = []
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if event is None or rec.get("event") == event:
                out.append(rec)
    return out


def write_csv(path: str | Path, rows: Iterable[dict[str, Any]]) -> int:
    """Write dict rows to CSV (union of keys as header). Returns the row count."""
    import csv
    rows = list(rows)
    keys: list[str] = []
    for r in rows:
        for k in r:
            if k not in keys:
                keys.append(k)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: json.dumps(v) if isinstance(v, (list, dict)) else v
                        for k, v in r.items()})
    return len(rows)

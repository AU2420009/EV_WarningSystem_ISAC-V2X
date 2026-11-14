#!/usr/bin/env python3
"""Deep-merge an overlay YAML over an upstream OpenISAC YAML.

    python scripts/apply_overlay.py UPSTREAM.yaml OVERLAY.yaml OUT.yaml

Mappings merge key by key; any other value (including lists such as simulation.targets)
in the overlay replaces the upstream one. Comments in the upstream file are not kept.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml


def merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = merge(base[k], v) if k in base else v
        return out
    return over


def main(argv: list[str] | None = None) -> int:
    a = sys.argv[1:] if argv is None else argv
    if len(a) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    base_p, over_p, out_p = map(Path, a)
    base = yaml.safe_load(base_p.read_text()) or {}
    over = yaml.safe_load(over_p.read_text()) or {}
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(yaml.safe_dump(merge(base, over), sort_keys=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

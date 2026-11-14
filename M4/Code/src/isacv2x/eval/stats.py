"""Small statistics helpers shared by the evaluation modules."""
from __future__ import annotations

import math
from typing import Sequence


def percentile(values: Sequence[float], q: float) -> float:
    """Nearest-rank percentile, q in [0, 100]. NaN for an empty sequence."""
    if not 0.0 <= q <= 100.0:
        raise ValueError("q must be in [0, 100]")
    if not values:
        return math.nan
    s = sorted(values)
    rank = max(1, math.ceil(q / 100.0 * len(s)))
    return s[rank - 1]

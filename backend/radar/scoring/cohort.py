"""Cohort percentiles with absolute anchors for small cohorts.

All scoring percentiles are *empirical* within the day's cohort (ties averaged).
Two guards keep small or degenerate cohorts honest:

* A measured value ``<= 0`` (no activity) always maps to percentile 0, so a
  cohort full of zeros does not hand out mid-range credit for doing nothing.
* When the cohort has fewer than ``cohort_min`` values for a feature, the
  empirical percentile is blended with an absolute, log-scale anchor
  ``p_abs = x / (x + mid)`` (0.5 at ``mid``) using ``alpha = n / cohort_min``.
  A lone entity is therefore *not* automatically the 100th percentile.

Anchor ``mid`` values live in ``ANCHORS`` below and are expressed in the same
(transformed) units the feature is compared in.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

# Feature key -> value at which the absolute anchor gives the 50th percentile.
ANCHORS: dict[str, float] = {
    # company technical momentum (log1p of a weekly rate)
    "stars_lv": math.log1p(20),  # ~20 stars/week
    "forks_lv": math.log1p(3),  # ~3 forks/week
    "commits_lv": math.log1p(15),  # ~15 commits/week
    "contributors_lv": math.log1p(1),  # ~1 new contributor/week
    # acceleration (second derivative)
    "star_acc": math.log1p(10),  # ~10 more stars this week than the prior 4-week average
    "commit_acc": math.log1p(0.5),  # commits up ~50% vs the prior 4 weeks
    # attention / technical composites
    "technical": 15.0,  # points of TM + acceleration (out of 45)
    "hn_points": math.log1p(50),
    "stars_total": math.log1p(500),
    "x_followers": math.log1p(1000),
    "press": math.log1p(3),
    # people
    "owned_stars": math.log1p(200),
    "recent_stars": math.log1p(20),  # ~20 stars/week on owned repos
    "commits": math.log1p(50),
    "followers": math.log1p(200),
    "hn_mentions": math.log1p(3),
    "tech_output": 0.5,  # weighted technical-output percentile blend
}


def ordinal(x: float) -> str:
    """Rounded percentile as an English ordinal: 1st, 2nd, 3rd, 11th, 61st."""
    n = round(x)
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def anchor_pct(key: str, x: float) -> float:
    mid = ANCHORS[key]
    return x / (x + mid) if x > 0 else 0.0


@dataclass
class Cohort:
    """Sorted per-feature value lists for one scoring day."""

    cohort_min: int
    values: dict[str, list[float]] = field(default_factory=dict)
    size: int = 0

    @classmethod
    def build(cls, rows: Iterable[Mapping[str, float | None]], cohort_min: int) -> Cohort:
        values: dict[str, list[float]] = {}
        n = 0
        for row in rows:
            n += 1
            for k, v in row.items():
                if v is not None:
                    values.setdefault(k, []).append(float(v))
        for v in values.values():
            v.sort()
        return cls(cohort_min=cohort_min, values=values, size=n)

    def n(self, key: str) -> int:
        return len(self.values.get(key, []))

    def pct(self, key: str, x: float | None) -> float | None:
        """Percentile 0..1 of ``x`` for feature ``key``; None if ``x`` is unmeasured."""
        if x is None:
            return None
        if x <= 0:
            return 0.0
        vals = self.values.get(key, [])
        n = len(vals)
        p_abs = anchor_pct(key, x)
        if n == 0:
            return p_abs
        lo, hi = bisect_left(vals, x), bisect_right(vals, x)
        emp = (lo + 0.5 * (hi - lo)) / n
        if n >= self.cohort_min:
            return emp
        alpha = n / self.cohort_min
        return alpha * emp + (1 - alpha) * p_abs

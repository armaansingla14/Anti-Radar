"""Technical Momentum (first derivative) and Acceleration (second derivative)."""

from __future__ import annotations

import math

from radar.scoring.cohort import Cohort, ordinal
from radar.scoring.features import Component, EntityFeatures, SignalLite, top_signals

TM_WEIGHTS: dict[str, float] = {
    "stars_lv": 0.40,
    "forks_lv": 0.20,
    "commits_lv": 0.25,
    "contributors_lv": 0.15,
}
_TM_LABELS = {
    "stars_lv": "stars (30d)",
    "forks_lv": "forks (30d)",
    "commits_lv": "commits (4w)",
    "contributors_lv": "new contributors (30d)",
}
MOMENTUM_SIGNAL_PREFIXES = (
    "star",
    "commit",
    "contributor",
    "new_contributor",
    "release",
    "fork",
    "repo_created",
)


def lv(delta: float | None, days: float) -> float | None:
    """Log weekly velocity: log1p(max(delta, 0) / days * 7). None stays None."""
    if delta is None:
        return None
    return math.log1p(max(delta, 0.0) / days * 7)


def star_acceleration(f: EntityFeatures) -> float | None:
    """Stars gained this week beyond the prior 4-week weekly average (log scale, clipped at 0)."""
    if f.stars_7d is None or f.stars_prev_28d is None:
        return None
    return math.log1p(max(f.stars_7d - f.stars_prev_28d / 4, 0.0))


def commit_acceleration(f: EntityFeatures) -> float | None:
    """Relative growth of commits vs the prior 4 weeks (log scale, clipped at 0)."""
    if f.commits_4w is None or f.commits_prev_4w is None:
        return None
    ratio = f.commits_4w / max(f.commits_prev_4w, 1.0) - 1
    return math.log1p(max(ratio, 0.0))


def momentum_row(f: EntityFeatures) -> dict[str, float | None]:
    """Transformed values used both for cohort distributions and for scoring."""
    return {
        "stars_lv": lv(f.stars_30d, 30),
        "forks_lv": lv(f.forks_30d, 30),
        "commits_lv": lv(f.commits_4w, 28),
        "contributors_lv": lv(f.new_contributors_30d, 30),
        "star_acc": star_acceleration(f),
        "commit_acc": commit_acceleration(f),
    }


def _is_momentum_signal(s: SignalLite) -> bool:
    return s.source_type == "github" and s.signal_type.startswith(MOMENTUM_SIGNAL_PREFIXES)


def technical_momentum(f: EntityFeatures, cohort: Cohort, max_points: float = 25) -> Component:
    row = momentum_row(f)
    pcts: dict[str, float] = {}
    for key in TM_WEIGHTS:
        p = cohort.pct(key, row[key])
        if p is not None:
            pcts[key] = p
    raw = {
        "stars_30d": f.stars_30d,
        "forks_30d": f.forks_30d,
        "commits_4w": f.commits_4w,
        "new_contributors_30d": f.new_contributors_30d,
        "percentiles": {k: round(v, 4) for k, v in pcts.items()},
        "missing": [k for k in TM_WEIGHTS if k not in pcts],
        "cohort_size": cohort.size,
    }
    evidence = top_signals(f.signals, _is_momentum_signal)
    if not pcts:
        return Component(
            "technical_momentum",
            "Technical Momentum",
            0.0,
            max_points,
            "No GitHub time series yet — momentum cannot be measured.",
            raw,
            evidence,
        )
    wsum = sum(TM_WEIGHTS[k] for k in pcts)
    blend = sum(TM_WEIGHTS[k] * p for k, p in pcts.items()) / wsum
    value = max_points * blend
    best = max(pcts, key=lambda k: pcts[k])
    expl = (
        f"Weighted cohort percentile {round(blend * 100)} across {len(pcts)} measured inputs; "
        f"strongest: {_TM_LABELS[best]} at the {ordinal(pcts[best] * 100)} percentile."
    )
    if raw["missing"]:
        expl += f" Not measured: {', '.join(_TM_LABELS[k] for k in raw['missing'])}."
    return Component("technical_momentum", "Technical Momentum", value, max_points, expl, raw, evidence)


def acceleration(
    f: EntityFeatures, cohort: Cohort, collision_score: float, collision_evidence, max_points: float = 20
) -> Component:
    """20 * (0.5 * acc + 0.5 * collision); without history, collision alone."""
    row = momentum_row(f)
    pcts = {k: p for k in ("star_acc", "commit_acc") if (p := cohort.pct(k, row[k])) is not None}
    raw: dict = {
        "stars_7d": f.stars_7d,
        "stars_prev_28d": f.stars_prev_28d,
        "commits_4w": f.commits_4w,
        "commits_prev_4w": f.commits_prev_4w,
        "collision": round(collision_score, 4),
        "percentiles": {k: round(v, 4) for k, v in pcts.items()},
    }
    evidence = list(collision_evidence)[:5]
    if pcts:
        acc = sum(pcts.values()) / len(pcts)
        raw["acc"] = round(acc, 4)
        value = max_points * (0.5 * acc + 0.5 * collision_score)
        expl = (
            f"Growth-rate change at the {ordinal(acc * 100)} percentile; "
            f"signal collision {collision_score:.2f}."
        )
    else:
        raw["acc"] = None
        raw["note"] = "no_history"
        value = max_points * collision_score
        expl = (
            f"No metric history for a second derivative yet; based on signal collision "
            f"{collision_score:.2f} only."
        )
    return Component("acceleration", "Acceleration", value, max_points, expl, raw, evidence)

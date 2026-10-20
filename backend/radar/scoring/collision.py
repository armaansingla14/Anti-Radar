"""Signal Collision: independent source families lighting up close together in time.

Each signal gets a weight ``w = confidence * strength * exp(-age / decay_days)``
(only within ``window_days``). Signals of the same family combine by noisy-OR
(``F = 1 - prod(1 - w)``), so a burst of same-source signals saturates at 1
instead of piling up. Families then add, with a bonus for each additional
*independent* family, and a multiplier if they landed within a tight span.

Only discrete events count (releases, papers, HN posts, jobs, affiliation changes…).
Measurement signals (``MEASUREMENT_SIGNAL_TYPES``) restate metric features that
Technical Momentum / Acceleration already score, and are dated at observation time,
so they are excluded here (they remain evidence elsewhere).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from radar.scoring.features import MEASUREMENT_SIGNAL_TYPES, SignalLite
from radar.thesis.loader import CollisionCfg

FAMILY_OF_SOURCE: dict[str, str] = {
    "github": "github",
    "arxiv": "arxiv",
    "hackernews": "hackernews",
    "website": "hiring/web",
    "jobs": "hiring/web",
    "x": "x",
    "manual": "manual",
}
# Display names for source families (UI / sentences). "hiring/web" is one *independent* family
# (a company's own website and its job board are not independent of each other); it is labeled by
# the source that actually contributed: jobs -> Hiring, website -> Web.
FAMILY_LABEL: dict[str, str] = {
    "github": "GitHub",
    "arxiv": "Research",
    "hackernews": "Community",
    "x": "Social",
    "manual": "Manual",
    "hiring/web": "Hiring",
}
SOURCE_LABEL: dict[str, str] = {
    "github": "GitHub",
    "arxiv": "Research",
    "jobs": "Hiring",
    "hiring": "Hiring",
    "website": "Web",
    "hackernews": "Community",
    "x": "Social",
    "manual": "Manual",
}
STRENGTH_HIGH_FAMILIES, STRENGTH_HIGH_SCORE = 3, 0.7
STRENGTH_MEDIUM_FAMILIES, STRENGTH_MEDIUM_SCORE = 2, 0.5
COLLISION_THESIS_FLOOR = 0.4  # ranking weight of a fully off-thesis collision (vs 1.0 on-thesis)
FAMILY_PRESENT_MIN = 0.15  # a family counts as "present" above this noisy-OR strength
DEFAULT_STRENGTH = 0.5
MAX_EVIDENCE = 10

_NUMBER_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
]


def number_word(n: int, capitalize: bool = False) -> str:
    w = _NUMBER_WORDS[n] if 0 <= n < len(_NUMBER_WORDS) else str(n)
    return w.capitalize() if capitalize else w


def collision_strength(n_families: int, score: float) -> str:
    """HIGH: >= 3 independent families and score >= 0.7; MEDIUM: >= 2 families or score >= 0.5; else LOW."""
    if n_families >= STRENGTH_HIGH_FAMILIES and score >= STRENGTH_HIGH_SCORE:
        return "HIGH"
    if n_families >= STRENGTH_MEDIUM_FAMILIES or score >= STRENGTH_MEDIUM_SCORE:
        return "MEDIUM"
    return "LOW"


def family_label(family: str, source_types: list[str] | tuple[str, ...] = ()) -> str:
    """Human family name. For the merged ``hiring/web`` family the contributing sources decide:
    any job posting -> Hiring, website only -> Web."""
    if family == "hiring/web":
        srcs = set(source_types)
        if "website" in srcs and not srcs & {"jobs", "hiring"}:
            return "Web"
        return "Hiring"
    return FAMILY_LABEL.get(family) or SOURCE_LABEL.get(family) or family.replace("_", " ").title()


def family_labels(families: list[str], evidence: list[dict[str, Any]] | None = None) -> list[str]:
    """Labels in family order; ``evidence`` (dicts with ``source_type``) disambiguates hiring/web."""
    srcs = [str(e.get("source_type")) for e in (evidence or []) if e.get("source_type")]
    out: list[str] = []
    for f in families:
        fam_srcs = [x for x in srcs if FAMILY_OF_SOURCE.get(x) == f] if f == "hiring/web" else []
        lab = family_label(f, fam_srcs)
        if lab not in out:
            out.append(lab)
    return out


def collision_sentence(n_families: int, span_days: float | None, signal_count: int,
                       labels: list[str], window_days: int) -> str:
    """'4 independent sources within 17 days' / 'One source (GitHub): 3 signals in 45 days' /
    'No recent independent signals'."""
    if n_families >= 2:
        return f"{n_families} independent sources within {max(1, round(span_days or 0))} days"
    if n_families == 1:
        lab = labels[0] if labels else "one family"
        return (f"One source ({lab}): {signal_count} signal{'s' if signal_count != 1 else ''} "
                f"in {window_days} days")
    return "No recent independent signals"


def collision_rank_weight(col: Any, thesis_fit_ratio: float | None) -> float:
    """Ranking-time weight of a collision: its score scaled by thesis fit (0..1 of max).

    An off-thesis collision keeps ``COLLISION_THESIS_FLOOR`` of its weight; an unknown fit counts as
    off-thesis (no fit is not evidence of fit). Accepts a Collision model, a CollisionResult or the
    stored dict; None -> 0."""
    if col is None:
        return 0.0
    score = col.get("score") if isinstance(col, dict) else getattr(col, "score", None)
    score = min(max(float(score or 0.0), 0.0), 1.0)
    fit = min(max(float(thesis_fit_ratio or 0.0), 0.0), 1.0)
    return round(score * (COLLISION_THESIS_FLOOR + (1 - COLLISION_THESIS_FLOOR) * fit), 4)


@dataclass
class CollisionResult:
    score: float
    families: list[str]
    family_strength: dict[str, float]
    window_days: int
    span_days: float | None
    signal_count: int
    contributions: list[tuple[SignalLite, float]] = field(default_factory=list)
    summary: str = ""

    @property
    def strength(self) -> str:
        return collision_strength(len(self.families), self.score)

    @property
    def family_labels(self) -> list[str]:
        out: list[str] = []
        for f in self.families:
            srcs = [s.source_type for s, _ in self.contributions if FAMILY_OF_SOURCE.get(s.source_type) == f]
            lab = family_label(f, srcs)
            if lab not in out:
                out.append(lab)
        return out

    @property
    def sentence(self) -> str:
        return collision_sentence(len(self.families), self.span_days, self.signal_count, self.family_labels,
                                  self.window_days)

    def to_api(self) -> dict[str, Any]:
        """Collision-shaped dict (schemas/api.py)."""
        return {
            "strength": self.strength,
            "family_labels": self.family_labels,
            "sentence": self.sentence,
            "score": round(self.score, 4),
            "families": self.families,
            "window_days": self.window_days,
            "span_days": None if self.span_days is None else round(self.span_days, 1),
            "signal_count": self.signal_count,
            "evidence": [s.evidence(w) for s, w in self.contributions[:MAX_EVIDENCE]],
            "summary": self.summary,
        }


def _as_dt(today: date | datetime) -> datetime:
    if isinstance(today, datetime):
        return today if today.tzinfo else today.replace(tzinfo=UTC)
    return datetime.combine(today, time(23, 59, 59), tzinfo=UTC)


def signal_weight(s: SignalLite, age_days: float, cfg: CollisionCfg) -> float:
    strength = s.normalized_value if s.normalized_value is not None else DEFAULT_STRENGTH
    strength = min(max(strength, 0.0), 1.0)
    conf = min(max(s.confidence, 0.0), 1.0)
    return conf * strength * math.exp(-max(age_days, 0.0) / cfg.decay_days)


def collision(signals: list[SignalLite], today: date | datetime, cfg: CollisionCfg) -> CollisionResult:
    now = _as_dt(today)
    by_family: dict[str, list[tuple[SignalLite, float]]] = {}
    seen: set = set()
    for s in signals:
        fam = FAMILY_OF_SOURCE.get(s.source_type)
        if fam is None or s.id in seen or s.signal_type in MEASUREMENT_SIGNAL_TYPES:
            continue
        seen.add(s.id)
        ev = s.event_at if s.event_at.tzinfo else s.event_at.replace(tzinfo=UTC)
        age = (now - ev).total_seconds() / 86400
        if age > cfg.window_days or age < -1:  # outside window, or implausibly in the future
            continue
        w = signal_weight(s, age, cfg)
        if w > 0:
            by_family.setdefault(fam, []).append((s, w))

    strength: dict[str, float] = {}
    for fam, items in by_family.items():
        prod = 1.0
        for _, w in items:
            prod *= 1 - w
        strength[fam] = 1 - prod

    present = sorted((f for f, v in strength.items() if v > FAMILY_PRESENT_MIN), key=lambda f: -strength[f])
    k = len(present)
    raw = sum(strength.values()) * (1 + cfg.independence_bonus * max(k - 1, 0))

    span: float | None = None
    if k >= 1:
        tops = [max(by_family[f], key=lambda sw: sw[1])[0].event_at for f in present]
        span = (max(tops) - min(tops)).total_seconds() / 86400
        if k >= 2 and span <= cfg.tight_span_days:
            raw *= cfg.tight_span_multiplier
    score = 1 - math.exp(-raw / cfg.saturation)

    contributions = sorted((sw for items in by_family.values() for sw in items), key=lambda sw: -sw[1])
    n_signals = len(contributions)
    if k >= 2:
        summary = f"{k} independent sources within {max(1, round(span or 0))} days"
    elif k == 1:
        summary = f"Single source family ({present[0]}): {n_signals} signal{'s' if n_signals != 1 else ''}"
    else:
        summary = "No strong recent signals"
    return CollisionResult(
        score=score,
        families=present,
        family_strength={f: round(v, 4) for f, v in strength.items()},
        window_days=cfg.window_days,
        span_days=span,
        signal_count=n_signals,
        contributions=contributions,
        summary=summary,
    )

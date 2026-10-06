"""Signal Collision: independent source families lighting up close together in time.

Each signal gets a weight ``w = confidence * strength * exp(-age / decay_days)``
(only within ``window_days``). Signals of the same family combine by noisy-OR
(``F = 1 - prod(1 - w)``), so a burst of same-source signals saturates at 1
instead of piling up. Families then add, with a bonus for each additional
*independent* family, and a multiplier if they landed within a tight span.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any

from radar.scoring.features import SignalLite
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

    def to_api(self) -> dict[str, Any]:
        """Collision-shaped dict (schemas/api.py)."""
        return {
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
        if fam is None or s.id in seen:
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

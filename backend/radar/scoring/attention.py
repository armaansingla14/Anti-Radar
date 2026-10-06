"""Attention Gap: strong technical signal that the public has not noticed yet."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from radar.scoring.cohort import Cohort, ordinal
from radar.scoring.features import Component, EntityFeatures
from radar.thesis.loader import AttentionCfg

ATTN_MAX_WEIGHT = 0.6  # weight of the strongest attention source vs the mean of all available


@dataclass
class AttentionResult:
    technical: float  # 0..100
    attention: float | None  # 0..100; None when no attention source is available
    gap: float
    flagged: bool
    coverage: str  # good | partial | low
    sources: list[dict[str, Any]] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        """AttentionGap-shaped dict (schemas/api.py)."""
        return {
            "technical": round(self.technical, 1),
            "attention": round(self.attention, 1) if self.attention is not None else 0.0,
            "gap": round(self.gap, 1),
            "flagged": self.flagged,
            "coverage": self.coverage,
            "sources": self.sources,
        }


def _log(v: float | None) -> float | None:
    return None if v is None else math.log1p(max(v, 0.0))


def attention_row(f: EntityFeatures) -> dict[str, float | None]:
    return {
        "hn_points": _log(f.hn_points_90d),
        "stars_total": _log(f.stars_total),
        "x_followers": _log(f.x_followers),
        "press": _log(f.press_mentions),
    }


_SOURCES = [
    ("hn_points", "Hacker News points (90d)", "hn_points_90d", "Hacker News: no data collected"),
    ("stars_total", "GitHub stars (total)", "stars_total", "GitHub: no repositories linked"),
    ("x_followers", "X followers", "x_followers", "X: unavailable — no API token or handle"),
    ("press", "Press mentions", "press_mentions", "Press: unavailable — no press source configured"),
]


def coverage_of(n_available: int) -> str:
    return "good" if n_available >= 3 else "partial" if n_available == 2 else "low"


def gap_result(technical: float, sources: list[dict[str, Any]], cfg: AttentionCfg) -> AttentionResult:
    """Combine a technical percentile (0..100) with per-source attention percentiles."""
    avail = [s["percentile"] for s in sources if s["available"]]
    # Attention leans on the strongest channel: if any source shows the entity is widely known,
    # it is known (a 90k-star repo with no HN post in our corpus is not "under the radar").
    attention = (ATTN_MAX_WEIGHT * max(avail) + (1 - ATTN_MAX_WEIGHT) * sum(avail) / len(avail)) if avail else None
    coverage = coverage_of(len(avail))
    gap = technical - (attention if attention is not None else 0.0)
    flagged = (
        attention is not None
        and technical >= cfg.flag_tech_min * 100
        and attention <= cfg.flag_attention_max * 100
    )
    return AttentionResult(technical, attention, gap, flagged, coverage, sources)


def attention_gap(
    f: EntityFeatures, cohort: Cohort, technical_points: float, cfg: AttentionCfg
) -> AttentionResult:
    """``technical_points`` = TM + Acceleration component values; percentile vs cohort key 'technical'."""
    technical = 100 * (cohort.pct("technical", technical_points) or 0.0)
    row = attention_row(f)
    sources = []
    for key, label, attr, note in _SOURCES:
        val = getattr(f, attr)
        p = cohort.pct(key, row[key])
        sources.append(
            {
                "key": key,
                "label": label,
                "available": p is not None,
                "value": val,
                "percentile": round(100 * p, 1) if p is not None else None,
                "note": None if p is not None else note,
            }
        )
    return gap_result(technical, sources, cfg)


def attention_component(res: AttentionResult, cfg: AttentionCfg, max_points: float = 10) -> Component:
    value = max_points * min(max(res.gap / 100, 0.0), 1.0)
    if res.coverage == "low":
        value *= cfg.low_coverage_penalty
    n_avail = sum(1 for s in res.sources if s["available"])
    if res.attention is None:
        expl = (
            f"Technical signal at the {ordinal(res.technical)} percentile but no public-attention source "
            f"is available; gap discounted for low coverage."
        )
    else:
        expl = (
            f"Technical signal at the {ordinal(res.technical)} percentile vs public attention at the "
            f"{ordinal(res.attention)} ({n_avail} source{'s' if n_avail != 1 else ''}, {res.coverage} coverage)."
        )
    raw = {
        "technical": round(res.technical, 1),
        "attention": None if res.attention is None else round(res.attention, 1),
        "gap": round(res.gap, 1),
        "coverage": res.coverage,
        "flagged": res.flagged,
    }
    return Component("attention_gap", "Attention Gap", value, max_points, expl, raw)

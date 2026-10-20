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
    gap: float  # technical - attention; 0 when attention is unmeasured (no gap can be claimed)
    flagged: bool
    coverage: str  # good | partial | low
    sources: list[dict[str, Any]] = field(default_factory=list)
    # What drives the technical side ("repo acceleration", "new contributors") — measured facts only.
    drivers: list[str] = field(default_factory=list)
    hn_threads: int | None = None  # measured HN stories (90d); None = not measured
    points: float | None = None  # Attention Gap component points (set by gap_points); None = no gap claimed

    @property
    def social_available(self) -> bool:
        return social_available(self.sources)

    @property
    def unavailable(self) -> list[str]:
        return unavailable_sources(self.sources)

    @property
    def explanation(self) -> str:
        return gap_explanation(self.technical, self.attention, self.sources, self.drivers, self.hn_threads)

    def to_api(self) -> dict[str, Any]:
        """AttentionGap-shaped dict (schemas/api.py)."""
        attention = round(self.attention, 1) if self.attention is not None else None
        return {
            "technical": round(self.technical, 1),
            "attention": attention,
            "gap": round(self.gap, 1),
            "flagged": self.flagged,
            "coverage": self.coverage,
            "sources": self.sources,
            "technical_score": round(self.technical, 1),
            "attention_score": attention,
            "gap_points": None if self.attention is None or self.points is None else round(self.points, 2),
            "explanation": self.explanation,
            "social_available": self.social_available,
            "unavailable": self.unavailable,
            "drivers": self.drivers,
            "hn_threads": self.hn_threads,
        }


SOCIAL_KEYS = ("x_followers",)
PRESS_KEYS = ("press",)
HN_KEYS = ("hn_points", "hn_mentions")


def social_available(sources: list[dict[str, Any]]) -> bool:
    """True only when an X / social source was actually measured."""
    return any(s.get("key") in SOCIAL_KEYS and s.get("available") for s in sources)


def unavailable_sources(sources: list[dict[str, Any]]) -> list[str]:
    """Labels of attention sources that could not be measured (never shown as zero)."""
    return [str(s.get("label") or s.get("key")) for s in sources if not s.get("available")]


def _fmt(v: float) -> str:
    return f"{int(round(v)):,}"


def _attention_phrases(sources: list[dict[str, Any]], hn_threads: int | None, low: bool) -> list[str]:
    by_key = {s.get("key"): s for s in sources}
    out: list[str] = []
    only = "only " if low else ""
    hn = next((by_key[k] for k in HN_KEYS if k in by_key), None)
    if hn_threads is not None:
        out.append("no Hacker News threads" if hn_threads == 0 else
                   f"{only}{hn_threads} HN thread{'s' if hn_threads != 1 else ''}")
    elif hn is not None and hn.get("available") and hn.get("value") is not None:
        v = float(hn["value"])
        noun = "HN points" if hn.get("key") == "hn_points" else "HN mentions"
        out.append(f"no {noun}" if v <= 0 else f"{only}{_fmt(v)} {noun}")
    stars = by_key.get("stars_total")
    if stars is not None and stars.get("available") and stars.get("value") is not None:
        out.append(f"{_fmt(float(stars['value']))} GitHub stars in total")
    fol = by_key.get("followers")
    if fol is not None and fol.get("available") and fol.get("value") is not None:
        out.append(f"{_fmt(float(fol['value']))} GitHub followers")
    press = next((by_key[k] for k in PRESS_KEYS if k in by_key), None)
    if press is not None:
        if press.get("available") and press.get("value") is not None:
            v = float(press["value"])
            out.append("no press" if v <= 0 else f"{_fmt(v)} press mention{'s' if v != 1 else ''}")
        else:
            out.append("press not measured")
    return out


def _join(parts: list[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def gap_explanation(
    technical: float,
    attention: float | None,
    sources: list[dict[str, Any]],
    drivers: list[str] | tuple[str, ...] = (),
    hn_threads: int | None = None,
) -> str:
    """'Technical momentum 88 vs public attention 29 — repo acceleration and new contributors with only
    2 HN threads and no press; social attention unavailable'. Built only from measured values; an
    unmeasured attention side is said to be unmeasured (no gap is claimed)."""
    social = "" if social_available(sources) else "; social attention unavailable"
    drv = _join(list(drivers))
    if attention is None:
        head = f"Technical momentum {technical:.0f}; public attention not measured — no gap claimed"
        return head + (f" ({drv})" if drv else "") + social
    head = f"Technical momentum {technical:.0f} vs public attention {attention:.0f}"
    att = _join(_attention_phrases(sources, hn_threads, low=attention <= 40))
    if drv and att:
        body = f" — {drv} with {att}"
    elif drv:
        body = f" — {drv}"
    elif att:
        body = f" — {att}"
    else:
        body = ""
    return head + body + social


def technical_drivers(f: EntityFeatures) -> list[str]:
    """Measured technical drivers, strongest-evidence first (thresholds match presenters.why_surfaced)."""
    out: list[str] = []
    if f.stars_7d is not None and f.stars_prev_28d and f.stars_prev_28d > 0 and (f.stars_30d or 0) >= 20:
        if (f.stars_7d / 7) / (f.stars_prev_28d / 28) >= 1.5:
            out.append("repo acceleration")
    elif (f.stars_30d or 0) >= 100:
        out.append("fast star growth")
    if f.commits_4w and f.commits_prev_4w and f.commits_prev_4w > 0 and f.commits_4w >= 15 \
            and f.commits_4w / f.commits_prev_4w >= 1.5:
        out.append("rising commit activity")
    if f.new_contributors_30d and f.new_contributors_30d >= 2:
        out.append("new contributors")
    if f.releases_90d and f.releases_90d >= 1 and len(out) < 3:
        out.append("recent releases")
    if f.technical_roles_30d and f.technical_roles_30d >= 2 and len(out) < 3:
        out.append("technical hiring")
    return out[:3]


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
    # Unmeasured attention is unknown, not zero: no gap is claimed (or scored) without a measurement.
    gap = technical - attention if attention is not None else 0.0
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
    res = gap_result(technical, sources, cfg)
    res.drivers = technical_drivers(f)
    res.hn_threads = f.hn_mentions_90d
    return res


def gap_points(res: AttentionResult, cfg: AttentionCfg, max_points: float) -> float:
    """Points for a measured gap; 0 when attention was not measured (unknown is not 'unnoticed').

    Also records the value on ``res.points`` (None when unmeasured) for the API's ``gap_points``."""
    if res.attention is None:
        res.points = None
        return 0.0
    value = max_points * min(max(res.gap / 100, 0.0), 1.0)
    if res.coverage == "low":
        value *= cfg.low_coverage_penalty
    res.points = value
    return value


def attention_component(res: AttentionResult, cfg: AttentionCfg, max_points: float = 10) -> Component:
    value = gap_points(res, cfg, max_points)
    n_avail = sum(1 for s in res.sources if s["available"])
    if res.attention is None:
        expl = (
            f"Technical signal at the {ordinal(res.technical)} percentile, but public attention is not "
            f"measured (no attention source available), so no gap is awarded."
        )
    else:
        expl = (
            f"Technical signal at the {ordinal(res.technical)} percentile vs public attention at the "
            f"{ordinal(res.attention)} ({n_avail} source{'s' if n_avail != 1 else ''}, {res.coverage} coverage)."
        )
    raw = {
        "technical": round(res.technical, 1),
        "attention": None if res.attention is None else round(res.attention, 1),
        "gap": None if res.attention is None else round(res.gap, 1),
        "coverage": res.coverage,
        "flagged": res.flagged,
    }
    return Component("attention_gap", "Attention Gap", value, max_points, expl, raw)

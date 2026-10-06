"""Learned preference multipliers from analyst feedback.

Keys: ``thesis:<theme>``, ``signal:<signal_type>``, ``stage:<stage>``, ``attention:known``.
Multipliers are bounded to ``[min_multiplier, max_multiplier]`` and their effect
on any total is capped at ``±max_point_shift`` points, so feedback nudges the
ranking but can never rewrite it.

Where each key acts (each exactly once, no double counting):
* ``thesis:*``  -> Thesis Fit component (thesis_fit.py)
* ``signal:*``, ``stage:*``, ``attention:known`` -> post-sum feedback adjustment here.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.models.core import Company, EntityTopic, Person, PreferenceWeight, Repository, Signal, Topic
from radar.thesis.loader import FeedbackCfg, Thesis, get_thesis

THEME_MIN_SCORE = 0.3
MAX_THEMES = 2
MAX_SIGNAL_TYPES = 3
CONTEXT_WINDOW_DAYS = 120
KNOWN_ATTENTION_MIN = 50.0  # attention percentile above which 'attention:known' applies


@dataclass
class Adjustment:
    multiplier: float
    shift: float
    applied: dict[str, float]


def clamp_multiplier(m: float, cfg: FeedbackCfg) -> float:
    return min(max(m, cfg.min_multiplier), cfg.max_multiplier)


def feedback_adjustment(
    base_total: float, keys: list[str], prefs: dict[str, float], cfg: FeedbackCfg
) -> Adjustment:
    applied = {k: prefs[k] for k in dict.fromkeys(keys) if k in prefs and prefs[k] != 1.0}
    mult = 1.0
    for v in applied.values():
        mult *= v
    mult = clamp_multiplier(mult, cfg)
    shift = base_total * (mult - 1)
    shift = min(max(shift, -cfg.max_point_shift), cfg.max_point_shift)
    return Adjustment(mult, shift, applied)


def adjustment_keys(signal_types: list[str], stage: str | None, attention: float | None) -> list[str]:
    keys = [f"signal:{t}" for t in signal_types]
    if stage:
        keys.append(f"stage:{stage}")
    if attention is not None and attention >= KNOWN_ATTENTION_MIN:
        keys.append("attention:known")
    return keys


def dominant_signal_types(signal_types: list[str], limit: int = MAX_SIGNAL_TYPES) -> list[str]:
    return [t for t, _ in Counter(signal_types).most_common(limit)]


def load_prefs(session: Session) -> dict[str, float]:
    return {pw.key: pw.multiplier for pw in session.scalars(select(PreferenceWeight))}


def entity_context(session: Session, entity_type: str, entity_id: uuid.UUID, day: date | None = None) -> dict:
    """Thesis themes, dominant signal types and stage of an entity at feedback time."""
    day = day or clock.today()
    ids = [entity_id]
    stage = None
    if entity_type == "company":
        ids += list(session.scalars(select(Repository.id).where(Repository.company_id == entity_id)))
        c = session.get(Company, entity_id)
        stage = c.stage if c else None
    rows = session.execute(
        select(Topic.theme, EntityTopic.score)
        .join(Topic, Topic.id == EntityTopic.topic_id)
        .where(EntityTopic.entity_id.in_(ids), EntityTopic.score >= THEME_MIN_SCORE)
    ).all()
    best: dict[str, float] = {}
    for theme, score in rows:
        best[theme] = max(best.get(theme, 0.0), score)
    themes = sorted(best, key=lambda t: -best[t])[:MAX_THEMES]
    since = datetime.combine(day - timedelta(days=CONTEXT_WINDOW_DAYS), time.min, tzinfo=UTC)
    types = list(
        session.scalars(select(Signal.signal_type).where(Signal.entity_id.in_(ids), Signal.event_at >= since))
    )
    return {"themes": themes, "signal_types": dominant_signal_types(types), "stage": stage}


def _bump(session: Session, key: str, delta: float, cfg: FeedbackCfg) -> PreferenceWeight:
    pw = session.get(PreferenceWeight, key)
    if pw is None:
        pw = PreferenceWeight(key=key, multiplier=1.0, n=0)
        session.add(pw)
    pw.multiplier = clamp_multiplier((pw.multiplier or 1.0) + delta, cfg)
    pw.n = (pw.n or 0) + 1
    return pw


def apply_feedback(
    session: Session, entity_type: str, entity_id: uuid.UUID, label: str, thesis: Thesis | None = None
) -> dict[str, float]:
    """Update PreferenceWeight rows for a feedback label; returns the weights that changed."""
    cfg = (thesis or get_thesis()).scoring.feedback
    ctx = entity_context(session, entity_type, entity_id)
    thesis_keys = [f"thesis:{t}" for t in ctx["themes"]]
    signal_keys = [f"signal:{t}" for t in ctx["signal_types"]]
    step = cfg.step
    changes: list[tuple[str, float]] = []
    if label == "useful":
        changes = [(k, step) for k in thesis_keys + signal_keys]
    elif label == "not_interesting":
        changes = [(k, -step) for k in thesis_keys + signal_keys]
    elif label == "wrong_thesis":
        changes = [(k, -step) for k in thesis_keys]
    elif label == "too_obvious":
        changes = [(k, -step) for k in signal_keys] + [("attention:known", -step)]
    elif label == "too_early":
        if ctx["stage"]:
            changes = [(f"stage:{ctx['stage']}", -step / 2)]
    elif label == "already_known":
        changes = [("attention:known", -step / 2)]
    elif label == "bad_data":
        model = Company if entity_type == "company" else Person if entity_type == "person" else None
        ent = session.get(model, entity_id) if model else None
        if ent is not None:
            ent.attrs = {
                **(ent.attrs or {}),
                "data_quality_flag": True,
                "data_quality_flagged_at": clock.now().isoformat(),
            }
    out: dict[str, float] = {}
    for key, delta in changes:
        out[key] = _bump(session, key, delta, cfg).multiplier
    session.flush()
    return out

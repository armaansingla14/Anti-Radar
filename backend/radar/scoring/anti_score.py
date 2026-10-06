"""ANTI SCORE (companies) and Founder Score (people) — deterministic, explainable, no LLM.

Cohort rule: percentiles are computed over the *non-demo* entities of the day.
Demo entities are scored against that same real cohort, so synthetic data can
never move a real entity's score. (If no real entities exist, the demo entities
form their own cohort.)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from radar.scoring.attention import AttentionResult, attention_component, attention_gap, attention_row
from radar.scoring.cohort import Cohort
from radar.scoring.collision import CollisionResult, collision
from radar.scoring.features import Component, EntityFeatures, PersonFeatures
from radar.scoring.feedback import adjustment_keys, dominant_signal_types, feedback_adjustment
from radar.scoring.founder import founder_score, founder_signal, person_row, technical_output
from radar.scoring.momentum import acceleration, momentum_row, technical_momentum
from radar.scoring.novelty import novelty
from radar.scoring.thesis_fit import thesis_fit
from radar.thesis.loader import Thesis

SCORE_VERSION = "2026.10-1"


@dataclass
class ScoreResult:
    entity_id: uuid.UUID
    entity_type: str
    total: int
    base_total: float
    feedback_adjustment: float
    components: list[Component]
    attention: AttentionResult
    collision: CollisionResult
    cohort_size: int
    is_demo: bool = False
    applied_prefs: dict[str, float] = field(default_factory=dict)
    version: str = SCORE_VERSION

    @property
    def low_coverage(self) -> bool:
        return self.attention.coverage == "low"

    def component(self, key: str) -> Component:
        return next(c for c in self.components if c.key == key)

    def components_json(self) -> dict[str, dict[str, Any]]:
        """{component_key: ScoreComponent dict} for ScoreSnapshot.components."""
        return {c.key: c.to_api() for c in self.components}

    def attention_json(self) -> dict[str, Any]:
        """AttentionGap dict plus score metadata (extra keys are ignored by the AttentionGap model)."""
        return {
            **self.attention.to_api(),
            "base_total": round(self.base_total, 2),
            "feedback_adjustment": round(self.feedback_adjustment, 2),
            "applied_prefs": self.applied_prefs,
            "cohort_size": self.cohort_size,
            "low_coverage": self.low_coverage,
        }


def _split_cohort[T: (EntityFeatures, PersonFeatures)](items: list[T]) -> list[T]:
    real = [x for x in items if not x.is_demo]
    return real if real else items


def _finalize(
    base: float, keys: list[str], prefs: dict[str, float], thesis: Thesis
) -> tuple[int, float, dict]:
    adj = feedback_adjustment(base, keys, prefs, thesis.scoring.feedback)
    total = int(min(max(round(base + adj.shift), 0), 100))
    return total, adj.shift, adj.applied


def score_companies(
    features: list[EntityFeatures], thesis: Thesis, prefs: dict[str, float], today: date
) -> dict[uuid.UUID, ScoreResult]:
    sc = thesis.scoring
    w = sc.weights
    pool = _split_cohort(features)
    pool_ids = {f.entity_id for f in pool}
    cohort = Cohort.build(({**momentum_row(f), **attention_row(f)} for f in pool), sc.cohort_min)

    first: dict[uuid.UUID, tuple[CollisionResult, Component, Component]] = {}
    for f in features:
        col = collision(f.signals, today, sc.collision)
        tm = technical_momentum(f, cohort, w["technical_momentum"])
        acc = acceleration(f, cohort, col.score, col.contributions, w["acceleration"])
        first[f.entity_id] = (col, tm, acc)
    cohort.values["technical"] = sorted(first[i][1].value + first[i][2].value for i in pool_ids)

    corpus = sorted(
        ((f.first_seen_at, f.name, f.tokens) for f in pool if f.first_seen_at is not None), key=lambda t: t[0]
    )
    out: dict[uuid.UUID, ScoreResult] = {}
    for f in features:
        col, tm, acc = first[f.entity_id]
        att = attention_gap(f, cohort, tm.value + acc.value, sc.attention_gap)
        earlier = [(n, t) for fs, n, t in corpus if f.first_seen_at is not None and fs < f.first_seen_at]
        comps = [
            tm,
            founder_signal(f, w["founder_signal"]),
            acc,
            thesis_fit(
                f.theme_scores, f.topic_scores, f.topic_evidence, thesis, prefs, f.stage, w["thesis_fit"]
            ),
            novelty(f.tokens, f.first_seen_at, earlier, today, sc.novelty.half_life_days, w["novelty"]),
            attention_component(att, sc.attention_gap, w["attention_gap"]),
        ]
        base = sum(c.value for c in comps)
        keys = adjustment_keys(
            dominant_signal_types([s.signal_type for s in f.signals]), f.stage, att.attention
        )
        total, shift, applied = _finalize(base, keys, prefs, thesis)
        out[f.entity_id] = ScoreResult(
            f.entity_id, "company", total, base, shift, comps, att, col, cohort.size, f.is_demo, applied
        )
    return out


def score_people(
    people: list[PersonFeatures], thesis: Thesis, prefs: dict[str, float], today: date
) -> dict[uuid.UUID, ScoreResult]:
    sc = thesis.scoring
    pool = _split_cohort(people)
    cohort = Cohort.build((person_row(p, today) for p in pool), sc.cohort_min)
    blends = {p.person_id: technical_output(p, cohort, today)[1] for p in pool}
    cohort.values["tech_output"] = sorted(b for b in blends.values() if b is not None)

    out: dict[uuid.UUID, ScoreResult] = {}
    for p in people:
        fs = founder_score(p, cohort, thesis, prefs, today)
        base = sum(c.value for c in fs.components)
        keys = adjustment_keys(
            dominant_signal_types([s.signal_type for s in p.signals]), None, fs.attention.attention
        )
        total, shift, applied = _finalize(base, keys, prefs, thesis)
        out[p.person_id] = ScoreResult(
            p.person_id,
            "person",
            total,
            base,
            shift,
            fs.components,
            fs.attention,
            fs.collision,
            cohort.size,
            p.is_demo,
            applied,
        )
    return out

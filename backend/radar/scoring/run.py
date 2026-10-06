"""Daily scoring run: features -> scores -> ScoreSnapshot + CompanyMetric rows."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar import clock
from radar.models.core import CompanyMetric, ScoreSnapshot
from radar.schemas.api import AntiScore, AttentionGap, Collision, ScoreComponent
from radar.scoring.anti_score import SCORE_VERSION, ScoreResult, score_companies, score_people
from radar.scoring.extract import build_company_features, build_person_features, load_signal_index
from radar.scoring.features import EntityFeatures
from radar.scoring.feedback import load_prefs
from radar.thesis.loader import Thesis, get_thesis

log = logging.getLogger(__name__)


def _upsert_snapshot(session: Session, r: ScoreResult, day: date, reconstructed: bool = False) -> None:
    attention = r.attention_json()
    if reconstructed:
        attention = {**attention, "reconstructed": True}
    else:
        # Like-for-like value for diffing against reconstructed history. A live run uses exactly the
        # reconstruction computation for this day: features are built only from data dated <= day, and
        # observation-dated measurement signals are excluded from collision (MEASUREMENT_SIGNAL_TYPES),
        # so nothing a reconstruction of today could not see enters the score.
        attention = {**attention, "reconstructed_score": float(r.total)}
    values = {
        "entity_type": r.entity_type,
        "entity_id": r.entity_id,
        "day": day,
        "score": float(r.total),
        "components": r.components_json(),
        "attention": attention,
        "collision": r.collision.to_api(),
        "version": r.version,
        "is_demo": r.is_demo,
    }
    stmt = insert(ScoreSnapshot).values(**values)
    session.execute(
        stmt.on_conflict_do_update(
            constraint="uq_score_day",
            set_={
                k: stmt.excluded[k]
                for k in (
                    "score",
                    "components",
                    "attention",
                    "collision",
                    "version",
                    "is_demo",
                    "entity_type",
                )
            },
        )
    )


def company_metrics(f: EntityFeatures, r: ScoreResult, day: date) -> dict[str, Any]:
    return {
        "computed_on": day.isoformat(),
        "score": r.total,
        "score_version": r.version,
        "stars_total": f.stars_total,
        "stars_7d": f.stars_7d,
        "stars_30d": f.stars_30d,
        "stars_prev_28d": f.stars_prev_28d,
        "forks_30d": f.forks_30d,
        "commits_4w": f.commits_4w,
        "commits_prev_4w": f.commits_prev_4w,
        "new_contributors_30d": f.new_contributors_30d,
        "contributors_total": f.contributors_total,
        "releases_90d": f.releases_90d,
        "open_roles": f.open_roles,
        "technical_roles_30d": f.technical_roles_30d,
        "hn_points_90d": f.hn_points_90d,
        "hn_mentions_90d": f.hn_mentions_90d,
        "x_followers": f.x_followers,
        "press_mentions": f.press_mentions,
        "attention_gap": round(r.attention.gap, 1),
        "attention_flagged": r.attention.flagged,
        "attention_coverage": r.attention.coverage,
        "collision": round(r.collision.score, 4),
        "collision_families": r.collision.families,
        "components": {c.key: round(c.value, 2) for c in r.components},
        "has_history": f.has_history,
    }


def score_all(
    session: Session, day: date | None = None, thesis: Thesis | None = None, *, reconstruct: bool = False
) -> dict[str, Any]:
    """Score every non-dismissed company and person for ``day`` (default: today) and persist snapshots.

    ``reconstruct=True`` rebuilds a *past* day's scores from data dated on or before that
    day (backfilled star/commit histories, signals). Snapshots are flagged
    ``attention.reconstructed`` and denormalized company metrics are left untouched."""
    day = day or clock.today()
    thesis = thesis or get_thesis()
    prefs = load_prefs(session)
    index = load_signal_index(session, day)

    people = build_person_features(session, day, index, thesis, reconstruct=reconstruct)
    person_scores = score_people(people, thesis, prefs, day)

    companies = build_company_features(session, day, index, thesis, reconstruct=reconstruct)
    for f in companies:
        known = [(pid, person_scores[pid].total) for pid in f.founder_ids if pid in person_scores]
        f.founder_ids = [pid for pid, _ in known]
        f.founder_scores = [float(s) for _, s in known]
    company_scores = score_companies(companies, thesis, prefs, day)

    for r in person_scores.values():
        _upsert_snapshot(session, r, day, reconstruct)
    for f in companies:
        r = company_scores[f.entity_id]
        _upsert_snapshot(session, r, day, reconstruct)
        if reconstruct:
            continue
        stmt = insert(CompanyMetric).values(company_id=f.entity_id, metrics=company_metrics(f, r, day))
        session.execute(
            stmt.on_conflict_do_update(
                index_elements=["company_id"],
                set_={"metrics": stmt.excluded.metrics, "updated_at": clock.now()},
            )
        )
    session.flush()
    summary = {
        "day": day.isoformat(),
        "version": SCORE_VERSION,
        "companies_scored": len(company_scores),
        "people_scored": len(person_scores),
        "company_cohort": sum(1 for f in companies if not f.is_demo),
        "people_cohort": sum(1 for p in people if not p.is_demo),
        "attention_flagged": sum(1 for r in company_scores.values() if r.attention.flagged),
    }
    log.info("score_all %s", summary)
    return summary


# jsonb does not preserve key order; present components in the order the score is defined.
COMPANY_ORDER = ("technical_momentum", "founder_signal", "acceleration", "thesis_fit", "novelty", "attention_gap")
FOUNDER_ORDER = ("technical_output", "research", "pedigree", "formation", "thesis_fit", "attention_gap")


def _ordered(components: dict[str, Any]) -> list[dict[str, Any]]:
    order = FOUNDER_ORDER if "technical_output" in components else COMPANY_ORDER
    rank = {k: i for i, k in enumerate(order)}
    return [components[k] for k in sorted(components, key=lambda k: rank.get(k, len(rank)))]


def anti_score_from_snapshot(
    snap: ScoreSnapshot, delta_7d: float | None = None, delta_30d: float | None = None
) -> AntiScore:
    """Rebuild the API AntiScore model from a stored ScoreSnapshot."""
    att = snap.attention or {}
    return AntiScore(
        total=round(snap.score),
        delta_7d=delta_7d,
        delta_30d=delta_30d,
        components=[ScoreComponent.model_validate(c) for c in _ordered(snap.components or {})],
        attention_gap=AttentionGap.model_validate(att) if att else None,
        collision=Collision.model_validate(snap.collision) if snap.collision else None,
        version=snap.version,
        computed_on=snap.day,
        feedback_adjustment=float(att.get("feedback_adjustment", 0.0)),
        adjustments=list(att.get("adjustments") or []),
        cohort_size=int(att.get("cohort_size", 0)),
        low_coverage=bool(att.get("low_coverage", False)),
    )

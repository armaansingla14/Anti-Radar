from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from radar.schemas.api import AttentionGap, Collision, ScoreComponent
from radar.scoring.anti_score import SCORE_VERSION, score_companies, score_people
from radar.scoring.extract import value_at, weekly_sum, window_delta
from radar.scoring.features import EntityFeatures, PersonFeatures, SignalLite
from radar.scoring.feedback import (
    adjustment_keys,
    clamp_multiplier,
    dominant_signal_types,
    feedback_adjustment,
)
from radar.thesis.loader import FeedbackCfg, get_thesis

TODAY = date(2026, 10, 6)
FCFG = FeedbackCfg()


@pytest.fixture(scope="module")
def thesis():
    return get_thesis()


def company(i: int, demo: bool = False, **kw) -> EntityFeatures:
    base = {
        "stars_total": float(100 * i),
        "stars_7d": float(i),
        "stars_30d": float(5 * i),
        "stars_prev_28d": float(4 * i),
        "forks_30d": float(i),
        "commits_4w": float(3 * i),
        "commits_prev_4w": float(3 * i),
        "new_contributors_30d": 1.0,
        "hn_points_90d": float(i),
        "first_seen_at": datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=i % 30),
        "theme_scores": {"robotics": 0.8},
        "tokens": {f"tok{i}", "robot"},
    }
    base.update(kw)
    return EntityFeatures(entity_id=uuid.uuid4(), name=f"c{i}", is_demo=demo, **base)


# ── feedback (pure) ──────────────────────────────────────────────────────────


def test_clamp_multiplier():
    assert (
        clamp_multiplier(5, FCFG) == FCFG.max_multiplier and clamp_multiplier(0, FCFG) == FCFG.min_multiplier
    )


@settings(max_examples=100, deadline=None)
@given(
    base=st.floats(min_value=0, max_value=100),
    mults=st.lists(st.floats(min_value=0.1, max_value=3.0), min_size=0, max_size=6),
)
def test_feedback_shift_bounded(base, mults):
    prefs = {f"signal:t{i}": m for i, m in enumerate(mults)}
    adj = feedback_adjustment(base, list(prefs), prefs, FCFG)
    assert -FCFG.max_point_shift <= adj.shift <= FCFG.max_point_shift
    assert FCFG.min_multiplier <= adj.multiplier <= FCFG.max_multiplier


def test_feedback_only_applies_matching_keys():
    adj = feedback_adjustment(50, ["signal:a"], {"signal:b": 1.2}, FCFG)
    assert adj.shift == 0 and adj.applied == {}


def test_adjustment_keys():
    keys = adjustment_keys(dominant_signal_types(["a", "b", "a", "c", "d", "a", "b"]), "seed", 80.0)
    assert keys == ["signal:a", "signal:b", "signal:c", "stage:seed", "attention:known"]
    assert "attention:known" not in adjustment_keys([], None, 10.0)


# ── series helpers ───────────────────────────────────────────────────────────


def test_value_at_nearest_prior_within_tolerance():
    pts = [(date(2026, 9, 1), 10.0), (date(2026, 9, 20), 50.0)]
    assert value_at(pts, date(2026, 9, 22), 3) == 50.0
    assert value_at(pts, date(2026, 9, 10), 3) is None  # stale baseline is not used
    assert value_at(pts, date(2026, 8, 1), 30) is None


def test_window_delta_missing_vs_new_repo():
    pts = sorted((TODAY - timedelta(days=d), 1000.0 - d) for d in range(40))
    assert window_delta(pts, TODAY, 0, 30, None) == 30
    assert window_delta(pts[-3:], TODAY, 0, 30, None) is None  # no history: None, not 0
    assert (
        window_delta([(TODAY, 120.0)], TODAY, 0, 30, TODAY - timedelta(days=4)) == 120
    )  # repo younger than window
    assert window_delta([], TODAY, 7, 35, TODAY - timedelta(days=2)) == 0  # did not exist in that window


def test_weekly_sum_scales_partial_and_needs_two():
    pts = sorted((TODAY - timedelta(days=7 * k), 10.0) for k in range(3))
    assert weekly_sum(pts, TODAY, range(4), None) == pytest.approx(40)
    assert weekly_sum(pts[-1:], TODAY, range(4), None) is None


# ── end-to-end pure scoring ──────────────────────────────────────────────────


def test_scores_bounded_and_api_shapes(thesis):
    feats = [company(i) for i in range(1, 25)]
    res = score_companies(feats, thesis, {}, TODAY)
    for r in res.values():
        assert 0 <= r.total <= 100 and r.version == SCORE_VERSION
        assert abs(sum(c.value for c in r.components) - r.base_total) < 1e-6
        assert {c.key for c in r.components} == set(thesis.scoring.weights)
        for c in r.components_json().values():
            ScoreComponent.model_validate(c)
        AttentionGap.model_validate(r.attention_json())
        Collision.model_validate(r.collision.to_api())


def test_demo_entities_do_not_move_real_scores(thesis):
    real = [company(i) for i in range(1, 15)]
    base = score_companies(real, thesis, {}, TODAY)
    demo = [company(1000 + i, demo=True, stars_30d=1e6, stars_7d=1e5) for i in range(5)]
    mixed = score_companies(real + demo, thesis, {}, TODAY)
    for f in real:
        assert mixed[f.entity_id].total == base[f.entity_id].total
    assert all(mixed[d.entity_id].total >= 0 for d in demo)


def test_feedback_preference_moves_total_within_cap(thesis):
    sig = SignalLite(
        uuid.uuid4(), "star_velocity", "github", "s", datetime(2026, 10, 5, tzinfo=UTC), 1.0, 0.9
    )
    feats = [company(i, signals=[sig]) for i in range(1, 15)]
    plain = score_companies(feats, thesis, {}, TODAY)
    liked = score_companies(feats, thesis, {"signal:star_velocity": 1.2}, TODAY)
    f = feats[-1]
    diff = liked[f.entity_id].total - plain[f.entity_id].total
    assert 0 < diff <= FCFG.max_point_shift
    assert liked[f.entity_id].applied_prefs == {"signal:star_velocity": 1.2}


def test_score_people_bounds(thesis):
    people = [
        PersonFeatures(
            person_id=uuid.uuid4(),
            name=f"p{i}",
            owned_repo_stars=float(i * 50),
            followers=float(i),
            papers_24mo=i % 3,
            tier=0.6 if i % 2 else 0.0,
        )
        for i in range(15)
    ]
    res = score_people(people, thesis, {}, TODAY)
    assert len(res) == 15
    for r in res.values():
        assert 0 <= r.total <= 100 and r.entity_type == "person"

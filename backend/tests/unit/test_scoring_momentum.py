from __future__ import annotations

import math
import uuid

from hypothesis import given, settings
from hypothesis import strategies as st

from radar.scoring.cohort import Cohort, anchor_pct
from radar.scoring.features import EntityFeatures
from radar.scoring.momentum import acceleration, lv, momentum_row, technical_momentum


def ent(**kw) -> EntityFeatures:
    return EntityFeatures(entity_id=uuid.uuid4(), name="e", **kw)


def cohort_of(feats, cohort_min=12) -> Cohort:
    return Cohort.build((momentum_row(f) for f in feats), cohort_min)


def crowd(n=30):
    return [
        ent(
            stars_30d=float(i * 10),
            forks_30d=float(i),
            commits_4w=float(i * 5),
            new_contributors_30d=float(i % 4),
        )
        for i in range(n)
    ]


# ── percentiles ──────────────────────────────────────────────────────────────


def test_pct_ties_averaged():
    c = Cohort.build([{"stars_lv": v} for v in (1.0, 2.0, 2.0, 3.0)], cohort_min=1)
    assert c.pct("stars_lv", 2.0) == (1 + 0.5 * 2) / 4


def test_pct_none_is_none_and_zero_is_zero():
    c = Cohort.build([{"stars_lv": v} for v in (0.0, 0.0, 0.0)], cohort_min=1)
    assert c.pct("stars_lv", None) is None
    assert c.pct("stars_lv", 0.0) == 0.0  # zero activity earns no credit even when everyone ties at zero


def test_lone_entity_is_not_100th_percentile():
    c = Cohort.build([{"stars_lv": 2.0}], cohort_min=12)
    p = c.pct("stars_lv", 2.0)
    assert p < 0.6
    assert math.isclose(p, (1 / 12) * 0.5 + (11 / 12) * anchor_pct("stars_lv", 2.0))


def test_empty_cohort_uses_anchor():
    c = Cohort.build([], cohort_min=12)
    assert c.pct("stars_lv", lv(140, 7)) == anchor_pct("stars_lv", lv(140, 7))
    assert anchor_pct("stars_lv", math.log1p(20)) == 0.5


def test_full_cohort_is_purely_empirical():
    c = Cohort.build([{"stars_lv": float(v)} for v in range(1, 13)], cohort_min=12)
    assert c.pct("stars_lv", 12.0) == (11 + 0.5) / 12


# ── technical momentum ───────────────────────────────────────────────────────


def test_lv_clips_negative_and_preserves_none():
    assert lv(None, 30) is None
    assert lv(-50, 30) == 0
    assert math.isclose(lv(30, 30), math.log1p(7))


def test_tm_bounds():
    feats = crowd()
    c = cohort_of(feats)
    for f in feats:
        v = technical_momentum(f, c).value
        assert 0 <= v <= 25


@settings(max_examples=100, deadline=None)
@given(base=st.floats(min_value=0, max_value=5000), extra=st.floats(min_value=0, max_value=5000))
def test_more_star_velocity_never_lowers_tm(base, extra):
    others = crowd(20)
    lo = ent(stars_30d=base, commits_4w=40.0)
    hi = ent(stars_30d=base + extra, commits_4w=40.0)
    v_lo = technical_momentum(lo, cohort_of(others + [lo])).value
    v_hi = technical_momentum(hi, cohort_of(others + [hi])).value
    assert v_hi >= v_lo - 1e-9


def test_missing_inputs_drop_out_and_renormalize():
    feats = crowd()
    c = cohort_of(feats)
    only_stars = ent(stars_30d=290.0)
    comp = technical_momentum(only_stars, c)
    assert set(comp.raw["missing"]) == {"forks_lv", "commits_lv", "contributors_lv"}
    assert math.isclose(comp.value, 25 * comp.raw["percentiles"]["stars_lv"], abs_tol=0.01)
    assert "Not measured" in comp.explanation


def test_no_history_is_zero_with_explanation_not_crash():
    comp = technical_momentum(ent(), cohort_of(crowd()))
    assert comp.value == 0 and "No GitHub time series" in comp.explanation


def test_none_differs_from_zero_in_tm():
    c = cohort_of(crowd())
    zero = technical_momentum(ent(stars_30d=0.0, commits_4w=300.0), c)
    missing = technical_momentum(ent(commits_4w=300.0), c)
    assert missing.value > zero.value  # zero stars is evidence; missing stars is not


# ── acceleration ─────────────────────────────────────────────────────────────


def test_acceleration_without_history_uses_collision_only():
    comp = acceleration(ent(), cohort_of(crowd()), 0.5, [])
    assert comp.value == 10 and comp.raw["note"] == "no_history"


def test_acceleration_rewards_speedup():
    feats = [
        ent(stars_7d=float(i), stars_prev_28d=float(4 * i), commits_4w=10.0, commits_prev_4w=10.0)
        for i in range(20)
    ]
    c = cohort_of(feats)
    steady = acceleration(
        ent(stars_7d=10.0, stars_prev_28d=40.0, commits_4w=10.0, commits_prev_4w=10.0), c, 0.2, []
    )
    faster = acceleration(
        ent(stars_7d=100.0, stars_prev_28d=40.0, commits_4w=30.0, commits_prev_4w=10.0), c, 0.2, []
    )
    assert faster.value > steady.value
    assert 0 <= steady.value <= 20 and 0 <= faster.value <= 20


def test_ordinal():
    from radar.scoring.cohort import ordinal

    assert [ordinal(x) for x in (1, 2, 3, 11, 12, 13, 21, 61.4, 100)] == [
        "1st",
        "2nd",
        "3rd",
        "11th",
        "12th",
        "13th",
        "21st",
        "61st",
        "100th",
    ]

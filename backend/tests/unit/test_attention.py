from __future__ import annotations

import uuid

from radar.scoring.attention import attention_component, attention_gap, attention_row, coverage_of
from radar.scoring.cohort import Cohort
from radar.scoring.features import EntityFeatures
from radar.thesis.loader import AttentionCfg

CFG = AttentionCfg()


def ent(**kw) -> EntityFeatures:
    return EntityFeatures(entity_id=uuid.uuid4(), name="e", **kw)


def cohort_of(feats: list[EntityFeatures], technical: list[float]) -> Cohort:
    c = Cohort.build((attention_row(f) for f in feats), cohort_min=3)
    c.values["technical"] = sorted(technical)
    return c


def _crowd():
    feats = [
        ent(hn_points_90d=float(i * 20), stars_total=float(i * 300), x_followers=None) for i in range(1, 21)
    ]
    return feats, cohort_of(feats, [float(i) for i in range(1, 21)])


def test_coverage_levels():
    assert coverage_of(3) == "good" and coverage_of(2) == "partial" and coverage_of(1) == "low"


def test_unavailable_sources_listed_with_notes():
    _, c = _crowd()
    r = attention_gap(ent(hn_points_90d=0.0, stars_total=100.0), c, 19.5, CFG)
    x = next(s for s in r.sources if s["key"] == "x_followers")
    assert x["available"] is False and "X: unavailable" in x["note"]
    assert r.coverage == "partial"


def test_flag_requires_high_tech_and_low_attention():
    _, c = _crowd()
    quiet = attention_gap(ent(hn_points_90d=0.0, stars_total=50.0), c, 20.0, CFG)
    assert quiet.flagged and quiet.gap > 50
    loud = attention_gap(ent(hn_points_90d=400.0, stars_total=6000.0), c, 20.0, CFG)
    assert not loud.flagged and loud.gap < quiet.gap
    weak = attention_gap(ent(hn_points_90d=0.0, stars_total=50.0), c, 2.0, CFG)
    assert not weak.flagged


def test_no_attention_sources_cannot_flag_and_is_penalised():
    _, c = _crowd()
    r = attention_gap(ent(), c, 20.0, CFG)
    assert r.attention is None and r.coverage == "low" and not r.flagged
    comp = attention_component(r, CFG)
    assert comp.value == 0  # unknown attention is not "unnoticed": no gap is awarded


def test_unmeasured_attention_is_null_in_api_not_zeroth_percentile():
    """#9: attention=None must reach the API as null, never 0.0, and no gap is claimed."""
    from radar.schemas.api import AttentionGap

    _, c = _crowd()
    r = attention_gap(ent(), c, 20.0, CFG)
    api = r.to_api()
    assert api["attention"] is None and api["gap"] == 0
    assert AttentionGap.model_validate(api).attention is None
    comp = attention_component(r, CFG)
    assert comp.raw["attention"] is None and comp.raw["gap"] is None
    assert "0th" not in comp.explanation and "not measured" in comp.explanation


def test_low_coverage_penalty_applied():
    _, c = _crowd()
    one = attention_gap(ent(hn_points_90d=0.0), c, 20.0, CFG)
    two = attention_gap(ent(hn_points_90d=0.0, stars_total=0.0), c, 20.0, CFG)
    assert one.coverage == "low" and two.coverage == "partial"
    assert attention_component(one, CFG).value < attention_component(two, CFG).value


def test_component_bounds_and_negative_gap_is_zero():
    _, c = _crowd()
    loud = attention_gap(ent(hn_points_90d=1e6, stars_total=1e7), c, 0.5, CFG)
    comp = attention_component(loud, CFG)
    assert loud.gap < 0 and comp.value == 0
    for t in (0.0, 5.0, 45.0):
        v = attention_component(attention_gap(ent(hn_points_90d=0.0, stars_total=0.0), c, t, CFG), CFG).value
        assert 0 <= v <= 10


def test_none_differs_from_zero():
    _, c = _crowd()
    measured_zero = attention_gap(ent(hn_points_90d=0.0), c, 10.0, CFG)
    unmeasured = attention_gap(ent(), c, 10.0, CFG)
    assert measured_zero.attention == 0.0 and unmeasured.attention is None

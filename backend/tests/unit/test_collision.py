from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from hypothesis import given, settings
from hypothesis import strategies as st

from radar.scoring.collision import collision, number_word
from radar.scoring.features import SignalLite
from radar.thesis.loader import CollisionCfg

TODAY = date(2026, 10, 6)
CFG = CollisionCfg()


def sig(source: str, days_ago: float = 1, strength: float | None = 0.8, conf: float = 1.0, stype: str = "x"):
    at = datetime(2026, 10, 6, 12, tzinfo=UTC) - timedelta(days=days_ago)
    return SignalLite(uuid.uuid4(), stype, source, f"{source} signal", at, conf, strength)


def test_empty_is_zero():
    r = collision([], TODAY, CFG)
    assert r.score == 0 and r.families == [] and r.span_days is None
    assert r.summary == "No strong recent signals"


@settings(max_examples=200, deadline=None)
@given(w=st.floats(min_value=0.2, max_value=1.0), n=st.integers(min_value=1, max_value=12))
def test_independent_families_beat_repeated_single_source(w, n):
    same = collision([sig("github", 1, w) for _ in range(n)], TODAY, CFG)
    diverse = collision([sig(s, 1, w) for s in ("github", "arxiv", "jobs", "x")], TODAY, CFG)
    assert diverse.score > same.score
    assert len(diverse.families) == 4 and len(same.families) == 1


def test_same_source_saturates():
    five = collision([sig("github") for _ in range(5)], TODAY, CFG)
    fifty = collision([sig("github") for _ in range(50)], TODAY, CFG)
    assert fifty.score - five.score < 0.01


def test_decay_older_signals_weigh_less():
    fresh = collision([sig("github", 1), sig("arxiv", 1)], TODAY, CFG)
    old = collision([sig("github", 30), sig("arxiv", 30)], TODAY, CFG)
    assert fresh.score > old.score > 0


def test_outside_window_ignored():
    r = collision([sig("github", CFG.window_days + 1)], TODAY, CFG)
    assert r.score == 0 and r.signal_count == 0


def test_tight_span_multiplier_applies():
    tight = collision([sig("github", 1), sig("arxiv", 5)], TODAY, CFG)
    loose = collision([sig("github", 1), sig("arxiv", 5 + 20)], TODAY, CFG)
    assert tight.score > loose.score
    assert tight.span_days is not None and round(tight.span_days) == 4


def test_website_and_jobs_are_one_family_and_summary():
    r = collision(
        [sig("website"), sig("jobs"), sig("hackernews", 18), sig("github", 2), sig("arxiv", 3)], TODAY, CFG
    )
    assert "hiring/web" in r.families and "jobs" not in r.families
    assert r.summary == "4 independent sources within 17 days"


def test_unknown_source_and_default_strength():
    r = collision([sig("system"), sig("github", strength=None)], TODAY, CFG)
    assert r.families == ["github"]
    assert r.contributions[0][1] < 0.5  # default strength 0.5 * decay


def test_weak_family_not_counted_present():
    r = collision([sig("github"), sig("x", strength=0.1)], TODAY, CFG)
    assert r.families == ["github"]


def test_score_bounded():
    r = collision(
        [sig(s, 0, 1.0) for s in ("github", "arxiv", "hackernews", "jobs", "x", "manual")] * 5, TODAY, CFG
    )
    assert 0 < r.score < 1


def test_number_words():
    assert number_word(3, True) == "Three" and number_word(15) == "15"


def test_measurement_signals_are_not_collision_events():
    """#5/#6: measurement signals restate metric features; they never count as collision."""
    from radar.scoring.features import MEASUREMENT_SIGNAL_TYPES

    for t in MEASUREMENT_SIGNAL_TYPES:
        r = collision([sig("github", 1, 0.9, stype=t)], TODAY, CFG)
        assert r.score == 0 and r.signal_count == 0 and r.families == []
    event = sig("github", 1, 0.9, stype="release_published")
    r = collision([event, sig("github", 1, 0.9, stype="star_velocity")], TODAY, CFG)
    assert r.signal_count == 1 and r.contributions[0][0].id == event.id


# ── Collision 2.0: strength, family labels, sentence, ranking weight ─────────


def test_strength_thresholds():
    from radar.scoring.collision import collision_strength

    assert collision_strength(3, 0.7) == "HIGH"
    assert collision_strength(3, 0.69) == "MEDIUM"  # near miss: score just under HIGH
    assert collision_strength(4, 0.2) == "MEDIUM"  # >= 2 families
    assert collision_strength(2, 0.1) == "MEDIUM"
    assert collision_strength(1, 0.5) == "MEDIUM"  # strong single family
    assert collision_strength(1, 0.49) == "LOW"
    assert collision_strength(0, 0.0) == "LOW"


def test_family_labels_and_sentence_in_api():
    r = collision(
        [sig("website"), sig("jobs"), sig("hackernews", 18), sig("github", 2), sig("arxiv", 3)], TODAY, CFG
    )
    api = r.to_api()
    assert set(api["family_labels"]) == {"Hiring", "Community", "GitHub", "Research"}
    assert api["sentence"] == "4 independent sources within 17 days"
    assert api["strength"] in ("HIGH", "MEDIUM")
    web_only = collision([sig("website"), sig("x")], TODAY, CFG)
    assert web_only.family_labels == ["Web", "Social"] or set(web_only.family_labels) == {"Web", "Social"}


def test_single_family_sentence_and_low_strength():
    r = collision([sig("github", 1, 0.3)], TODAY, CFG)
    assert r.families == ["github"] and r.strength == "LOW"
    assert r.sentence == f"One source (GitHub): 1 signal in {CFG.window_days} days"
    assert collision([], TODAY, CFG).sentence == "No recent independent signals"


def test_schema_derives_v2_fields_for_old_snapshots():
    from radar.schemas.api import Collision

    old = {"score": 0.8, "families": ["github", "arxiv", "hiring/web"], "window_days": 60, "span_days": 12.4,
           "signal_count": 5, "summary": "3 independent sources within 12 days",
           "evidence": [{"title": "Site", "source_type": "website"}]}
    c = Collision.model_validate(old)
    assert c.strength == "HIGH" and c.family_labels == ["GitHub", "Research", "Web"]
    assert c.sentence == "3 independent sources within 12 days"
    fresh = Collision.model_validate(collision([sig("github"), sig("arxiv")], TODAY, CFG).to_api())
    assert fresh.family_labels == ["GitHub", "Research"] or set(fresh.family_labels) == {"GitHub", "Research"}


def test_collision_rank_weight_thesis_scaling():
    from radar.scoring.collision import COLLISION_THESIS_FLOOR, collision_rank_weight

    r = collision([sig("github"), sig("arxiv"), sig("jobs")], TODAY, CFG)
    on = collision_rank_weight(r, 1.0)
    off = collision_rank_weight(r, 0.0)
    assert on == round(r.score, 4) and off == round(r.score * COLLISION_THESIS_FLOOR, 4)
    assert collision_rank_weight(r.to_api(), None) == off  # unknown fit counts as off-thesis
    assert collision_rank_weight(None, 1.0) == 0.0
    assert collision_rank_weight({"score": 2.0}, 5.0) == 1.0  # bounded

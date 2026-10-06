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

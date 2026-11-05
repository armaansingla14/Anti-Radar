"""SINCE LAST SNAPSHOT pure helpers: formation status, materiality, score comparability."""

from __future__ import annotations

from datetime import date

import pytest

from radar.models import ScoreSnapshot
from radar.services.changes import (
    dominant_version,
    formation_status,
    material_delta,
    score_comparability,
    strength_of,
    versions_comparable,
)

B, C = date(2026, 10, 20), date(2026, 11, 5)


def row(tier: str, score: float) -> dict:
    return {"tier": tier, "score": score}


@pytest.mark.parametrize(("base", "cur", "expected"), [
    (None, None, None),
    (None, row("early", 31), "NEW"),
    (row("early", 31), None, "DROPPED"),
    (row("early", 45), row("emerging", 51), "STRENGTHENED"),  # tier up decides even on a small move
    (row("high", 72), row("emerging", 69), "WEAKENED"),  # tier down
    (row("emerging", 55), row("emerging", 60), "STRENGTHENED"),  # same tier, +5
    (row("emerging", 60), row("emerging", 55), "WEAKENED"),  # same tier, -5
    (row("emerging", 55), row("emerging", 59.9), "UNCHANGED"),  # same tier, below threshold
    (row("early", 40), row("early", 40), "UNCHANGED"),
    (row("emerging", 64), row("high", 58), "STRENGTHENED"),  # tier up wins over a score drop
])
def test_formation_status_matrix(base, cur, expected):
    assert formation_status(base, cur) == expected


def test_formation_status_accepts_objects():
    class R:
        def __init__(self, tier, score):
            self.tier, self.score = tier, score

    assert formation_status(R("early", 30), R("early", 36)) == "STRENGTHENED"


@pytest.mark.parametrize(("delta", "expected"), [
    (None, None), (0.0, None), (4.9, None), (4.96, "up"), (5.0, "up"), (12.3, "up"),
    (-4.9, None), (-5.0, "down"), (-20, "down"),
])
def test_material_delta(delta, expected):
    assert material_delta(delta) == expected


def test_material_delta_custom_threshold():
    assert material_delta(2.0, 2.0) == "up" and material_delta(1.9, 2.0) is None


def snap(score: float, *, version: str = "v1", reconstructed: bool = False) -> ScoreSnapshot:
    att = {"reconstructed": True} if reconstructed else {"reconstructed_score": score}
    return ScoreSnapshot(entity_type="company", score=score, version=version, attention=att, components={})


def test_comparable_when_live_same_version_and_reobserved():
    assert score_comparability(snap(40), snap(48), True, base_day=B, current_day=C) == (True, None)


def test_missing_baseline_is_not_comparable():
    ok, reason = score_comparability(None, snap(48), True, base_day=B, current_day=C)
    assert not ok and "No score on 2026-10-20" in reason and "new to Radar" in reason


def test_missing_current_is_not_comparable():
    ok, reason = score_comparability(snap(40), None, True, base_day=B, current_day=C)
    assert not ok and "2026-11-05" in reason


def test_reconstructed_baseline_is_not_comparable():
    ok, reason = score_comparability(snap(40, reconstructed=True), snap(48), True, base_day=B, current_day=C)
    assert not ok and "reconstructed" in reason and "2026-10-20" in reason


def test_version_mismatch_is_not_comparable():
    ok, reason = score_comparability(snap(40, version="v1"), snap(48, version="v2"), True)
    assert not ok and "v1 → v2" in reason


def test_not_reobserved_is_not_comparable():
    ok, reason = score_comparability(snap(40), snap(48), False, base_day=B, current_day=C)
    assert not ok and "Not re-observed since 2026-10-20" in reason


def test_structural_checks_come_before_reobservation():
    ok, reason = score_comparability(snap(40, version="a"), snap(48, version="b"), False)
    assert not ok and "version" in reason


def test_versions_comparable():
    assert versions_comparable(["v1", "v1"], ["v1"]) == (True, None, "v1", "v1")
    ok, reason, b, c = versions_comparable(["v1", "v1", "v0"], ["v2", "v2"])
    assert not ok and (b, c) == ("v1", "v2") and "v1 → v2" in reason
    ok, reason, _, _ = versions_comparable([], ["v1"])
    assert not ok and "base day" in reason
    assert dominant_version([None, "b", "a", "a"]) == "a"
    assert dominant_version(["a", "b"]) == "b"  # deterministic tie-break


def test_strength_of_derives_missing_strength():
    assert strength_of(None) is None and strength_of({}) is None
    assert strength_of({"strength": "HIGH"}) == "HIGH"
    assert strength_of({"families": ["github", "arxiv", "hackernews"], "score": 0.8}) == "HIGH"
    assert strength_of({"families": ["github"], "score": 0.1}) == "LOW"

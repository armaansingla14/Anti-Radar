"""Feedback v2 labels: bounded preference weights + stored reason (scoring.feedback, services.watchlist)."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from radar.db import session_scope
from radar.models import Company, CompanyPerson, UserFeedback
from radar.scoring.feedback import (
    FOUNDER_KEY,
    TEAM_KEY,
    adjustment_keys,
    feedback_adjustment,
    person_boost,
    person_key,
)
from radar.thesis.loader import FeedbackCfg


def _id(model, slug):
    with session_scope() as s:
        return str(s.scalar(select(model.id).where(model.slug == slug)))


def test_aggregate_keys_attach_only_with_matching_signals():
    assert FOUNDER_KEY in adjustment_keys(["new_project", "release_published"], None, None)
    assert TEAM_KEY in adjustment_keys(["lab_alumni_cluster"], "formation", None)
    keys = adjustment_keys(["release_published", "hn_post"], "seed", 10.0)
    assert FOUNDER_KEY not in keys and TEAM_KEY not in keys


def test_new_labels_are_bounded():
    cfg = FeedbackCfg()
    adj = feedback_adjustment(60.0, [FOUNDER_KEY, TEAM_KEY], {FOUNDER_KEY: 9.0, TEAM_KEY: 9.0}, cfg)
    assert adj.multiplier <= cfg.max_multiplier and abs(adj.shift) <= cfg.max_point_shift
    assert person_boost({}, "abc") == 1.0
    assert person_boost({person_key("abc"): 5.0}, "abc", cfg) == cfg.max_multiplier


def test_interesting_matches_useful(seeded, client):
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": hid, "label": "interesting"})
    assert r.status_code == 200
    changed = r.json()["changed"]
    assert changed["thesis:robotics"] == pytest.approx(1.05)
    assert any(k.startswith("signal:") for k in changed)


def test_strong_founder_boosts_founder_key_and_person(seeded, client):
    with session_scope() as s:
        cp = s.scalar(select(CompanyPerson).where(CompanyPerson.is_founder.is_(True)))
        pid, cid = str(cp.person_id), str(cp.company_id)
    r = client.post("/api/feedback", json={"entity_type": "person", "entity_id": pid, "label": "strong_founder",
                                           "note": "shipped two control stacks"})
    changed = r.json()["changed"]
    assert changed == {FOUNDER_KEY: pytest.approx(1.05), person_key(pid): pytest.approx(1.05)}
    # On a company it boosts the company's founders.
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": cid, "label": "strong_founder"})
    assert person_key(pid) in r.json()["changed"]
    with session_scope() as s:
        fb = s.scalar(select(UserFeedback).where(UserFeedback.entity_id == uuid.UUID(pid)))
        assert fb.label == "strong_founder" and fb.note == "shipped two control stacks"
        assert fb.context["reason"] == "shipped two control stacks"
        assert FOUNDER_KEY in fb.context["preference_changes"]


def test_interesting_shift_and_weak_team(seeded, client):
    hid = _id(Company, "halcyon-dynamics")
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": hid, "label": "interesting_shift"})
    changed = r.json()["changed"]
    assert len(changed) == 1 and next(iter(changed)).startswith("signal:")  # only the dominant type
    r = client.post("/api/feedback", json={"entity_type": "company", "entity_id": hid, "label": "weak_team"})
    assert r.json()["changed"] == {TEAM_KEY: pytest.approx(0.95)}
    with session_scope() as s:
        fb = s.scalars(select(UserFeedback).where(UserFeedback.label == "weak_team")).one()
        assert fb.context["reason"] is None


def test_unknown_label_still_rejected(seeded, client):
    assert client.post("/api/feedback", json={"entity_type": "company", "entity_id": "loomwork",
                                              "label": "meh"}).status_code == 422

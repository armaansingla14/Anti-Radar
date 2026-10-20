"""Pure parts of the Who-Should-We-Meet ranking (services/meet.py): weeks, priority/confidence,
score composition, diversity and brand-account detection. No database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from radar.services import meet


def test_iso_week_uses_new_york_time():
    # Monday 2026-10-12 02:00 UTC is still Sunday evening in New York → previous ISO week
    assert meet.iso_week(datetime(2026, 10, 12, 2, 0, tzinfo=UTC)) == "2026-W41"
    assert meet.iso_week(datetime(2026, 10, 12, 5, 0, tzinfo=UTC)) == "2026-W42"
    assert meet.iso_week(datetime(2027, 1, 1, 12, 0, tzinfo=UTC)) == "2026-W53"


def test_resolve_week_validation():
    assert meet.resolve_week("2026-W07") == "2026-W07"
    assert meet.resolve_week("current") == meet.iso_week()
    for bad in ("2026-W00", "2026-W54", "2026-7", "last", "2026W07"):
        with pytest.raises(meet.InvalidWeek):
            meet.resolve_week(bad)


def test_priority_and_confidence_need_breadth():
    assert meet.confidence_for(3) == "HIGH CONFIDENCE"
    assert meet.confidence_for(2) == "MEDIUM CONFIDENCE"
    assert meet.confidence_for(1) == meet.confidence_for(0) == "EARLY SIGNAL"
    assert meet.priority_for(80, 2) == "HIGH"
    assert meet.priority_for(80, 1) == "MEDIUM"  # a high score from one family is not HIGH priority
    assert meet.priority_for(45, 3) == "MEDIUM"
    assert meet.priority_for(20, 3) == "WATCH"


def test_weights_sum_to_100_and_score_floors_at_zero():
    assert sum(meet.WEIGHTS.values()) == 100
    assert meet.meet_score({"founder": 5.0, "obviousness": -25.0}) == 0.0
    assert meet.meet_score({"founder": 21.0, "formation": 17.5, "feedback": -6}) == 32.5


def _r(score: float, founder: float = 50, group: str | None = None, pid: str | None = None):
    return SimpleNamespace(score=score, founder_score=founder, group=group,
                           person=SimpleNamespace(id=uuid.UUID(pid) if pid else uuid.uuid4()))


def test_select_diverse_caps_groups_and_orders_deterministically():
    a, b, c = (_r(90, group="formation:x"), _r(80, group="formation:x"), _r(70, group="formation:x"))
    d, e = _r(60, group=None), _r(60, founder=70, group="company:acme")
    picked = meet.select_diverse([c, d, a, e, b], limit=5)
    assert picked == [a, b, e, d]  # third member of formation:x dropped; tie broken by founder score
    t1 = _r(50, pid="00000000-0000-0000-0000-000000000002")
    t2 = _r(50, pid="00000000-0000-0000-0000-000000000001")
    assert meet.select_diverse([t1, t2]) == [t2, t1]  # full tie → person id
    assert len(meet.select_diverse([_r(i) for i in range(10)], limit=5)) == 5


def test_brand_account_detection():
    keys = {"maker s pet": "makerspet", "makerspet": "makerspet", "deeplethe": "deeplethe"}
    brand = SimpleNamespace(name="Maker's Pet", github_handle="makers-pet")
    person = SimpleNamespace(name="Wayland Yang", github_handle="WaylandYang")
    org_itself = SimpleNamespace(name="DeepLethe", github_handle="deeplethe")  # the org's own login
    assert meet.is_brand_account(brand, keys)
    assert not meet.is_brand_account(person, keys)
    assert not meet.is_brand_account(org_itself, keys)


def test_evidence_families_and_newest_change_skip_measurements():
    now = datetime(2026, 10, 6, 18, tzinfo=UTC)

    def sig(st, stype, days):
        return SimpleNamespace(source_type=st, signal_type=stype, event_at=now - timedelta(days=days))

    ev = meet.Evidence([sig("github", "star_velocity", 0), sig("arxiv", "paper_published", 5),
                        sig("hackernews", "hn_show", 200)])
    assert ev.families(now - timedelta(days=90)) == ["github", "research"]
    assert ev.newest_change(now).signal_type == "paper_published"  # measurement dates are observation times


def test_affiliation_line_marks_self_reported():
    p = SimpleNamespace(current_company="@acme-robotics", prior_affiliations=["NVIDIA"],
                        attrs={"self_reported_fields": ["company"]})
    line = meet.affiliation_line(p)
    assert "(self-reported)" in line and "prev. NVIDIA" in line
    assert meet.affiliation_line(SimpleNamespace(current_company=None, prior_affiliations=[], attrs={})) \
        == "Affiliation unknown"
    assert meet.affiliation_line(p, ["Acme"]).startswith("Founder, Acme")


# ── talent pool ──────────────────────────────────────────────────────────────


def _labs(commits=412, tier=1):
    from radar.scoring.features import parse_lab_contributions

    return parse_lab_contributions([{"org": "google-deepmind", "label": "Google DeepMind", "tier": tier,
                                     "repo": "mujoco", "commits": commits, "first_commit_at": "2024-01-01",
                                     "last_commit_at": "2025-08-01"}])


def test_talent_points_prefer_a_recent_formation_event():
    labs = _labs()
    assert meet.talent_points(labs, True) == meet.TALENT_POINTS
    assert meet.talent_points(labs, False) == meet.TALENT_POINTS * meet.TALENT_NO_EVENT_SHARE
    assert meet.talent_points([], True) == 0
    assert meet.talent_points(_labs(commits=5, tier=2), True) < meet.talent_points(labs, True)
    assert meet.meet_score({"founder": 30, "formation": 25, "thesis": 10, "recency": 10, "attention": 10,
                            "collision": 15, "talent": 8}) == 100.0  # capped


def _ranked(labs, signals, comps=None):
    now = datetime(2026, 10, 6, 18, tzinfo=UTC)
    snap = SimpleNamespace(score=48.0, components=comps or {}, attention={})
    return SimpleNamespace(person=SimpleNamespace(attrs={}), snap=snap, founder_score=48.0, labs=labs,
                           candidate=None, obvious=None, evidence=meet.Evidence(signals)), now


def test_why_person_leads_with_verified_history_and_why_now_with_the_new_project():
    now = datetime(2026, 10, 6, 18, tzinfo=UTC)

    def sig(stype, days, title):
        return SimpleNamespace(id=uuid.uuid4(), signal_type=stype, source_type="github", title=title, description=None,
                               source_url="https://github.com/x", event_at=now - timedelta(days=days))

    new = sig("new_project", 40, "Ada created ada/tactile-sim")  # older than 30 days, still leads why-now
    stars = sig("star_velocity", 1, "ada/tactile-sim +40 stars")
    comps = {"pedigree": {"value": 15, "max": 15, "explanation": "Contributed 412 commits to ...",
                          "raw": {"source": "lab_contribution"}},
             "technical_output": {"value": 10, "max": 25, "explanation": "Technical output measured."}}
    r, _ = _ranked(_labs(), [stars, new], comps)
    why = meet.why_person(r, [])
    assert why[0] == "412 commits to google-deepmind/mujoco (tier-1 lab repository, verified)"
    assert why[1] == "Founder Score 48/100"
    assert not any(w.startswith("Contributed 412") for w in why)  # pedigree not repeated
    assert not any("worked at" in w.lower() for w in why)
    items = meet.why_now(r, now)
    assert items[0].title == "Ada created ada/tactile-sim" and items[1].title == "ada/tactile-sim +40 stars"
    assert meet.formation_events(r.evidence) == [new]
    # An unknown contributor: no verified line, Founder Score first.
    r2, _ = _ranked([], [stars], comps)
    assert meet.why_person(r2, [])[0] == "Founder Score 48/100"

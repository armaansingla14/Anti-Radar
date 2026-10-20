"""Team Convergence: only relationships that predate the anchor count; deterministic; explained."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from radar.scoring.convergence import (
    COAUTHORED_WITH,
    CONTRIBUTED_WITH,
    WORKED_WITH,
    Member,
    RelLite,
    convergence,
    prior_ties,
    strength_label,
)

A, B, C = (uuid.UUID(int=i) for i in (1, 2, 3))
MEMBERS = [Member(A, "Ada"), Member(B, "Ben"), Member(C, "Cy")]
CREATED = date(2026, 8, 1)


def paper(a, b, when: str, title="Tactile VLA") -> RelLite:
    return RelLite(uuid.uuid4(), COAUTHORED_WITH, a, b, "VERIFIED",
                   [{"title": title, "detail": "arXiv:1", "source_url": "https://arxiv.org/abs/1",
                     "source_type": "arxiv", "event_at": f"{when}T00:00:00+00:00"}])


def worked(a, b, org="NVIDIA", prior=True) -> RelLite:
    return RelLite(uuid.uuid4(), WORKED_WITH, a, b, "INFERRED", [
        {"title": f"x lists {org}", "org": org, "org_key": org.lower(), "prior": prior, "event_at": None},
        {"title": f"y lists {org}", "org": org, "org_key": org.lower(), "prior": prior, "event_at": None},
    ])


def cocontrib(a, b, when: str, repo_id: str, title="org/old-repo") -> RelLite:
    return RelLite(uuid.uuid4(), CONTRIBUTED_WITH, a, b, "VERIFIED",
                   [{"title": title, "source_type": "github", "event_at": f"{when}T00:00:00+00:00",
                     "repo_id": repo_id}])


def test_paper_after_anchor_creation_does_not_count():
    rels = [paper(A, B, "2026-09-01")]
    res = convergence(MEMBERS, rels, CREATED, anchor_name="acme")
    assert res.prior_relationships == 0 and res.connected_members == 0
    assert "No relationship among the 3 members predates acme" in res.explanation


def test_paper_before_anchor_counts_and_is_explained():
    res = convergence(MEMBERS, [paper(A, B, "2025-11-03")], CREATED, anchor_name="acme")
    assert res.prior_relationships == 1 and res.connected_members == 2
    assert "2 of 3 members co-authored a paper in 2025" in res.explanation
    assert res.relationship_ids and res.kinds == [COAUTHORED_WITH]


def test_cocontribution_on_anchor_repo_is_not_prior_history():
    anchor_repo = str(uuid.uuid4())
    own = convergence(MEMBERS, [cocontrib(A, B, "2026-05-01", anchor_repo)], CREATED, anchor_name="acme",
                      anchor_repo_ids={anchor_repo})
    other = convergence(MEMBERS, [cocontrib(A, B, "2026-05-01", str(uuid.uuid4()))], CREATED, anchor_name="acme",
                        anchor_repo_ids={anchor_repo})
    assert own.prior_relationships == 0 and other.prior_relationships == 1
    assert "contributed to org/old-repo together earlier" in other.explanation


def test_undated_cocontribution_cannot_predate():
    r = cocontrib(A, B, "2026-05-01", "r")
    r.evidence[0]["event_at"] = None
    assert prior_ties([r], CREATED) == []


def test_shared_prior_employer_is_self_reported_and_anchor_org_excluded():
    res = convergence(MEMBERS, [worked(A, B), worked(A, C), worked(B, C)], CREATED, anchor_name="acme")
    assert "All three list NVIDIA as a prior employer (self-reported)" in res.explanation
    assert res.connected_members == 3
    # A self-reported shared employer alone is never HIGH convergence.
    assert res.strength == "MEDIUM"
    same = convergence(MEMBERS, [worked(A, B, org="Acme", prior=False)], CREATED, anchor_name="acme",
                       anchor_org_keys={"acme"})
    assert same.prior_relationships == 0


def test_current_shared_employer_weaker_than_prior():
    prior = convergence(MEMBERS[:2], [worked(A, B, prior=True)], CREATED, anchor_name="acme")
    current = convergence(MEMBERS[:2], [worked(A, B, prior=False)], CREATED, anchor_name="acme")
    assert prior.score > current.score
    assert "as an affiliation" in current.explanation


def test_more_coverage_kinds_and_recency_raise_score():
    one = convergence(MEMBERS, [paper(A, B, "2025-11-01")], CREATED, anchor_name="acme")
    two = convergence(MEMBERS, [paper(A, B, "2025-11-01"), worked(A, C), worked(B, C)], CREATED, anchor_name="acme")
    assert two.score > one.score and two.kinds == [COAUTHORED_WITH, WORKED_WITH]
    old = convergence(MEMBERS[:2], [paper(A, B, "2019-01-01")], CREATED, anchor_name="acme")
    recent = convergence(MEMBERS[:2], [paper(A, B, "2026-06-01")], CREATED, anchor_name="acme")
    assert recent.score > old.score


def test_families_alone_are_not_convergence():
    res = convergence(MEMBERS, [], CREATED, anchor_name="acme", families=["github", "arxiv", "hackernews", "x"])
    assert res.score <= 0.08 and res.strength == "LOW"
    assert "four independent source families" in res.explanation


def test_bounds_strength_and_determinism():
    rels = [paper(A, B, "2026-07-01"), paper(B, C, "2026-07-02"), worked(A, C), cocontrib(A, C, "2026-06-01", "r")]
    r1 = convergence(MEMBERS, rels, datetime(2026, 8, 1, tzinfo=UTC), anchor_name="acme",
                     families=["github", "arxiv", "x"])
    r2 = convergence(MEMBERS, list(reversed(rels)), CREATED, anchor_name="acme", families=["x", "github", "arxiv"])
    assert 0.0 <= r1.score <= 1.0 and r1.strength == "HIGH"
    assert round(r1.score, 6) == round(r2.score, 6) and r1.explanation == r2.explanation
    assert strength_label(0.6) == "HIGH" and strength_label(0.35) == "MEDIUM" and strength_label(0.1) == "LOW"


def test_relationships_outside_member_set_ignored():
    outsider = uuid.UUID(int=99)
    res = convergence(MEMBERS, [paper(A, outsider, "2025-01-01")], CREATED, anchor_name="acme")
    assert res.prior_relationships == 0


def test_cocontribution_on_other_repo_before_creation_counts_after_does_not():
    before = convergence(MEMBERS[:2], [cocontrib(A, B, "2026-03-01", "other")], CREATED, anchor_name="acme",
                         anchor_repo_ids={"anchor"})
    after = convergence(MEMBERS[:2], [cocontrib(A, B, "2026-09-01", "other")], CREATED, anchor_name="acme",
                        anchor_repo_ids={"anchor"})
    assert before.prior_relationships == 1 and before.kinds == [CONTRIBUTED_WITH]
    assert after.prior_relationships == 0 and after.score < before.score


def test_undated_cocontribution_is_explained_not_scored():
    r = cocontrib(A, B, "2026-05-01", "other")
    r.evidence[0]["event_at"] = None
    res = convergence(MEMBERS[:2], [r], CREATED, anchor_name="acme", anchor_repo_ids={"anchor"})
    base = convergence(MEMBERS[:2], [], CREATED, anchor_name="acme")
    assert res.score == base.score and res.undated_cocontributions == 1
    assert "One co-contribution on other repositories has no first-commit dates" in res.explanation
    assert "cannot be shown to predate acme" in res.explanation
    # undated co-contribution on the anchor's own repo is not "elsewhere"
    own = convergence(MEMBERS[:2], [r], CREATED, anchor_name="acme", anchor_repo_ids={"other"})
    assert own.undated_cocontributions == 0


def test_joined_within_window_is_explained_and_unknown_dates_said():
    joined = {A: datetime(2026, 8, 3, tzinfo=UTC), B: datetime(2026, 8, 20, tzinfo=UTC),
              C: datetime(2026, 10, 1, tzinfo=UTC)}
    res = convergence(MEMBERS, [], CREATED, anchor_name="acme", joined=joined)
    assert set(res.founding_members) == {A, B} and res.join_dates_unknown == 0
    assert "Ada and Ben made their first commits to acme within 30 days of its creation" in res.explanation
    base = convergence(MEMBERS, [], CREATED, anchor_name="acme")
    assert res.score == base.score  # team-formation evidence, not prior history
    unknown = convergence(MEMBERS, [], CREATED, anchor_name="acme", joined={A: None, B: None, C: None})
    assert unknown.founding_members == [] and unknown.join_dates_unknown == 3
    assert "First-commit dates on acme are unknown for all three" in unknown.explanation
    assert "founding_members" in res.to_json() and res.to_json()["join_dates_unknown"] == 0


def test_founding_members_window():
    from radar.scoring.convergence import founding_members

    got, unknown = founding_members({A: date(2026, 7, 25), B: date(2026, 8, 31), C: None}, CREATED, 30)
    assert got == sorted([A, B], key=str) and unknown == 1

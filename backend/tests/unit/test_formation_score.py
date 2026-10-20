"""FORMATION SCORE: bounded components, determinism, tiers, honest wording."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta

from radar.scoring.convergence import COAUTHORED_WITH, Member, RelLite, convergence
from radar.scoring.features import SignalLite
from radar.scoring.formation import (
    FORMATION_MAX,
    LABEL_PERSON,
    LABEL_SIGNALS,
    LABEL_TEAM,
    CandidateInput,
    EvidenceFact,
    MemberInfo,
    build_tech_cohort,
    formation_evidence,
    label_for,
    score_candidate,
    tech_row,
    tier_for,
)

TODAY = date(2026, 10, 6)
NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)
A, B, C = (uuid.UUID(int=i) for i in (1, 2, 3))


def sig(stype: str, source: str, days_ago: float, title: str | None = None) -> SignalLite:
    return SignalLite(uuid.uuid4(), stype, source, title or f"{stype} event", NOW - timedelta(days=days_ago),
                      1.0, 0.7, f"https://example.org/{stype}", False, None, A)


def team(**kw) -> CandidateInput:
    members = [MemberInfo(A, "Ada", founder_score=72, tier=1.0, tier_match="NVIDIA", self_reported=True,
                          profile_url="https://github.com/ada"),
               MemberInfo(B, "Ben", founder_score=55), MemberInfo(C, "Cy", founder_score=None)]
    rels = [RelLite(uuid.uuid4(), COAUTHORED_WITH, A, B, "VERIFIED",
                    [{"title": "Paper", "source_type": "arxiv", "event_at": "2026-03-01T00:00:00+00:00"}])]
    conv = convergence([Member(m.id, m.name) for m in members], rels, date(2026, 8, 1), anchor_name="acme",
                       families=["github", "arxiv", "hackernews"])
    facts = [
        EvidenceFact("org_created", "GitHub organization acme created 2026-08-01", source_type="github",
                     event_at=datetime(2026, 8, 1, 12, tzinfo=UTC)),
        EvidenceFact("first_job", "First job posting: Founding Engineer", source_type="jobs",
                     event_at=NOW - timedelta(days=3)),
        EvidenceFact("launch", "Show HN: acme", source_type="hackernews", event_at=NOW - timedelta(days=5)),
        EvidenceFact("website", "Website listed: acme.dev", source_type="website"),
    ]
    base = dict(
        kind="team", anchor_type="company", anchor_id=uuid.UUID(int=50), anchor_name="acme", anchor_slug="acme",
        created=date(2026, 8, 1), first_seen_at=NOW - timedelta(days=10), members=members,
        metrics={"commits_4w": 120.0, "stars_30d": 400.0, "new_contributors_30d": 3.0, "releases_90d": 2.0},
        facts=facts, signals=[sig("paper_published", "arxiv", 20), sig("hn_show", "hackernews", 5),
                              sig("star_velocity", "github", 1)],
        attention={"technical": 90, "attention": 30, "gap": 60, "coverage": "medium"}, thesis_fit=0.9,
        theme="robotics", convergence=conv,
    )
    base.update(kw)
    return CandidateInput(**base)


def cohort():
    return build_tech_cohort(tech_row(m, "team") for m in [
        {"commits_4w": 10.0, "stars_30d": 20.0, "new_contributors_30d": 0.0, "releases_90d": 0.0},
        {"commits_4w": 40.0, "stars_30d": 50.0, "new_contributors_30d": 1.0, "releases_90d": 1.0},
        {"commits_4w": 120.0, "stars_30d": 400.0, "new_contributors_30d": 3.0, "releases_90d": 2.0},
    ])


def test_components_are_bounded_and_sum_to_score():
    sc = score_candidate(team(), cohort(), TODAY)
    assert list(sc.components) == list(FORMATION_MAX)
    for key, c in sc.components.items():
        assert 0 <= c["value"] <= FORMATION_MAX[key] == c["max"]
        assert c["explanation"] and isinstance(c["raw"], dict)
        for e in c["evidence"]:
            assert e["title"]
    assert abs(sc.score - sum(c["value"] for c in sc.components.values())) < 0.02
    assert 0 <= sc.score <= 100


def test_extremes_stay_in_bounds():
    big = team(members=[MemberInfo(uuid.uuid4(), f"m{i}", founder_score=100, tier=1.0, tier_match="MIT")
                        for i in range(20)],
               metrics={"commits_4w": 1e6, "stars_30d": 1e6, "new_contributors_30d": 1e4, "releases_90d": 1e3},
               facts=[EvidenceFact(k, k) for k in ("org_created", "working_team", "first_job", "website", "launch",
                                                   "formation_language", "affiliation_change", "young_org")] * 5,
               attention={"technical": 100, "attention": 0, "gap": 100}, first_seen_at=NOW)
    sc = score_candidate(big, cohort(), TODAY)
    for key, c in sc.components.items():
        assert 0 <= c["value"] <= FORMATION_MAX[key]
    empty = team(members=[], metrics={}, facts=[], signals=[], attention=None, first_seen_at=None, convergence=None)
    sc0 = score_candidate(empty, {}, TODAY)
    assert sc0.score == 0 and sc0.tier is None and sc0.label is None


def test_deterministic():
    a = score_candidate(team(), cohort(), TODAY)
    b = score_candidate(team(), cohort(), TODAY)
    strip = lambda x: re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", json.dumps(x))  # noqa: E731
    assert a.score == b.score and strip(a.components) == strip(b.components) and a.families == b.families


def test_insufficient_history_is_honest():
    sc = score_candidate(team(metrics={"commits_4w": None, "stars_30d": None, "new_contributors_30d": None,
                                       "releases_90d": None}), cohort(), TODAY)
    ta = sc.components["technical_activity"]
    assert ta["value"] == 0 and "Insufficient history" in ta["explanation"] and ta["raw"]["insufficient_history"]


def test_unmeasured_attention_gives_no_credit():
    sc = score_candidate(team(attention={"technical": 90, "attention": None, "gap": None}), cohort(), TODAY)
    na = sc.components["novelty_attention"]
    assert "not measured" in na["explanation"] and na["raw"]["attention_points"] == 0


def test_more_distinct_evidence_kinds_more_points():
    one = formation_evidence([EvidenceFact("org_created", "x"), EvidenceFact("org_created", "y")])
    three = formation_evidence([EvidenceFact(k, k) for k in ("org_created", "first_job", "launch")])
    assert 0 < one["value"] < three["value"] <= FORMATION_MAX["formation_evidence"]
    assert three["raw"]["count"] == 3 and len(three["evidence"]) == 3


def test_tiers_and_labels():
    # HIGH: score >= 70, >= 3 families AND convergence >= MEDIUM.
    assert tier_for(75, 3, "MEDIUM") == "high" and tier_for(75, 4, "HIGH") == "high"
    assert tier_for(75, 3, "LOW") == "emerging" and tier_for(75, 3) == "emerging"
    assert tier_for(75, 2, "HIGH") == "emerging"
    # EMERGING: score >= 50 AND >= 2 families; single-family is at most EARLY.
    assert tier_for(50, 2) == "emerging" and tier_for(90, 1, "HIGH") == "early"
    assert tier_for(49.9, 3) == "early" and tier_for(30, 0) == "early" and tier_for(29.9, 5) is None
    assert label_for("team", "high") == label_for("team", "emerging") == LABEL_TEAM
    assert label_for("team", "early") == LABEL_SIGNALS
    assert label_for("person", "high") == label_for("person", "early") == LABEL_PERSON
    assert label_for("team", None) is None


def test_person_candidate_has_no_convergence_credit():
    p = team(kind="person", anchor_type="person", anchor_name="Ada", members=[MemberInfo(A, "Ada", founder_score=60)],
             convergence=None, metrics={"commits_30d": 30.0, "stars_30d": 10.0})
    sc = score_candidate(p, build_tech_cohort([tech_row(p.metrics, "person")]), TODAY)
    assert sc.components["team_convergence"]["value"] == 0
    assert "Single person" in sc.components["team_convergence"]["explanation"]
    assert sc.title == "Ada — possible formation"


def test_never_says_founded_and_marks_self_reported():
    sc = score_candidate(team(), cohort(), TODAY)
    text = json.dumps([sc.title, sc.label, sc.components, sc.why_now]).lower()
    assert "founded" not in text and "founder of" not in text
    assert "self-reported" in sc.components["founder_quality"]["explanation"]


def test_why_now_is_dated_newest_first_and_families_counted():
    sc = score_candidate(team(), cohort(), TODAY)
    dates = [w["event_at"] for w in sc.why_now]
    assert dates and all(dates) and dates == sorted(dates, reverse=True)
    assert set(sc.families) >= {"github", "research", "community", "hiring"}
    assert set(sc.families) <= {"github", "research", "hiring", "web", "community", "social"}
    assert sc.independent_signals >= len(sc.families)
    assert sc.tier in ("high", "emerging") and sc.title == "Emerging team around acme"


def test_single_family_candidate_is_never_above_early():
    gh = dict(source_type="github", event_at=datetime(2026, 8, 1, 12, tzinfo=UTC))
    gh_only = team(facts=[EvidenceFact("org_created", "GitHub organization acme created", **gh),
                          EvidenceFact("working_team", "3 contributors", **gh),
                          EvidenceFact("founding_team", "Three members committed within 30 days", **gh)],
                   signals=[sig("star_velocity", "github", 1), sig("young_org_team", "github", 2)],
                   convergence=None)
    sc = score_candidate(gh_only, cohort(), TODAY)
    assert sc.families == ["github"]
    assert sc.score >= 50 and sc.tier == "early" and sc.label == LABEL_SIGNALS


def test_high_requires_medium_convergence():
    loose = team(convergence=convergence([Member(A, "Ada"), Member(B, "Ben")], [], date(2026, 8, 1),
                                         anchor_name="acme", families=["github", "research", "community"]))
    sc = score_candidate(loose, cohort(), TODAY)
    assert len(sc.families) >= 3 and sc.inp.convergence.strength == "LOW"
    assert sc.tier != "high"


def test_prior_paper_tie_counts_as_research_family():
    from radar.scoring.formation import families_of

    conv = team().convergence
    fams, _ = families_of([], [], conv.ties)
    assert fams == ["research"]  # the co-authored paper predating the org is attached evidence


def test_member_signals_exclude_unrelated_member_repos():
    """Regression (DeepLethe): a member's activity on an unrelated repository is not team evidence."""
    from radar.scoring.formation import member_signals

    repo = uuid.UUID(int=77)  # a member's unrelated repo (e.g. bilawalsidhu/gods-eye-view)
    stray = SignalLite(uuid.uuid4(), "new_contributors", "github", "someone/gods-eye-view: 45 new contributors",
                       NOW, entity_id=repo)
    own_star = SignalLite(uuid.uuid4(), "star_velocity", "github", "Ada's side project +500 stars", NOW, entity_id=A)
    aff = SignalLite(uuid.uuid4(), "affiliation_changed", "github", "Ada: NVIDIA -> Stealth", NOW, entity_id=A)
    lang = SignalLite(uuid.uuid4(), "formation_language", "github", "Ben's bio now mentions: stealth", NOW,
                      entity_id=B)
    launch_other = SignalLite(uuid.uuid4(), "x_launch_post", "x", "Launching my weekend app", NOW, entity_id=A)
    launch_anchor = SignalLite(uuid.uuid4(), "x_launch_post", "x", "Launching Acme today", NOW, entity_id=A)
    others = SignalLite(uuid.uuid4(), "affiliation_changed", "github", "Outsider moved", NOW, entity_id=uuid.UUID(int=9))
    got = member_signals([stray, own_star, aff, lang, launch_other, launch_anchor, others], {A, B}, {"acme"})
    assert {x.id for x in got} == {aff.id, lang.id, launch_anchor.id}
    p = team(signals=[sig("star_velocity", "github", 1), *got])
    sc = score_candidate(p, cohort(), TODAY)
    cited = json.dumps([sc.why_now, sc.components])
    assert "gods-eye-view" not in cited and "side project" not in cited


def test_generic_founder_only():
    from radar.scoring.formation import generic_founder_only

    bio = SignalLite(uuid.uuid4(), "formation_language", "github", "Ada's bio now mentions: co-founder", NOW,
                     entity_id=A)
    fact = EvidenceFact("formation_language", bio.title, event_at=NOW, signal_id=bio.id)
    assert generic_founder_only([fact], [bio], {bio.id: ["co-founder"]})
    assert not generic_founder_only([fact], [bio], {bio.id: ["co-founder", "stealth"]})
    assert generic_founder_only([fact], [bio], {})  # no phrase metadata: falls back to the title
    aff = SignalLite(uuid.uuid4(), "affiliation_changed", "github", "Ada: X -> Y", NOW, entity_id=A)
    assert not generic_founder_only([fact, EvidenceFact("affiliation_change", aff.title, event_at=NOW)],
                                    [bio, aff], {bio.id: ["co-founder"]})
    assert not generic_founder_only([fact, EvidenceFact("young_org", "Linked to acme")], [bio],
                                    {bio.id: ["co-founder"]})


# ── public GitHub org memberships and curated-list anchors ───────────────────


def test_founder_quality_explains_public_org_membership():
    from radar.scoring.formation import founder_quality

    m = MemberInfo(A, "Ada", founder_score=60, tier=1.0, tier_match="OpenAI", self_reported=True,
                   tier_basis="public_github_org", tier_org_login="openai", profile_url="https://github.com/ada")
    c = founder_quality([m, MemberInfo(B, "Ben", founder_score=40)])
    assert "Ada is a public GitHub member of openai (OpenAI, tier-1)" in c["explanation"]
    assert "self-reported" not in c["explanation"]
    assert c["raw"]["tier_basis"] == "public_github_org" and c["raw"]["tier_self_reported"] is False
    assert "public GitHub member of openai" in c["evidence"][0]["detail"]


def test_curated_list_anchor():
    from radar.models.core import Company, Repository
    from radar.scoring.formation import curated_list_anchor

    co = Company(name="V-Modal", github_org="v-modal")
    lst = Repository(full_name="v-modal/awesome-jev-tools", owner="v-modal", stars=752,
                     description="A curated list of tools", topics=["awesome-list"])
    eng = Repository(full_name="v-modal/engine", owner="v-modal", stars=10, description="Visual memory", topics=[])
    assert curated_list_anchor(co, [lst]) == "all repositories are curated lists"
    assert "top repository v-modal/awesome-jev-tools" in curated_list_anchor(co, [lst, eng])
    big = Repository(full_name="v-modal/engine", owner="v-modal", stars=900, description="Visual memory", topics=[])
    assert curated_list_anchor(co, [lst, big]) is None  # a real product leads; the list is a side repo
    assert curated_list_anchor(co, []) is None


# ── talent pool: verified lab contributions ──────────────────────────────────


def _labs(*rows):
    from radar.scoring.features import parse_lab_contributions

    return parse_lab_contributions([{"org": o, "label": o, "tier": t, "repo": r, "commits": n,
                                     "first_commit_at": f, "last_commit_at": last}
                                    for o, r, n, t, f, last in rows])


def test_founder_quality_prefers_verified_lab_history_over_self_reported():
    from radar.scoring.formation import founder_quality

    lab = MemberInfo(A, "Ada", founder_score=40, lab_contributions=_labs(
        ("google-deepmind", "mujoco", 412, 1, "2024-01-01", "2025-09-01")))
    claimed = MemberInfo(A, "Ada", founder_score=40, tier=1.0, tier_match="NVIDIA", tier_basis="affiliation",
                         self_reported=True)
    v_lab, v_claim = founder_quality([lab]), founder_quality([claimed])
    assert v_lab["value"] > v_claim["value"]
    assert "Ada: contributed 412 commits to google-deepmind/mujoco (tier-1 lab repository, verified)" \
        in v_lab["explanation"]
    assert v_lab["raw"]["tier_basis"] == "lab_contribution" and not v_lab["raw"]["tier_self_reported"]
    assert v_claim["raw"]["tier_self_reported"] and v_claim["raw"]["best_tier"] == 0.7
    assert "412 commits to google-deepmind/mujoco (verified)" in v_lab["evidence"][0]["detail"]
    assert "worked at" not in v_lab["explanation"].lower()


def _talent_team(with_lab: bool) -> CandidateInput:
    created = date(2026, 8, 1)
    labs = {A: _labs(("NVIDIA", "cutlass", 412, 1, "2024-03-01", "2025-05-01")),
            B: _labs(("NVIDIA", "cutlass", 57, 1, "2024-06-01", "2025-02-01"))} if with_lab else {}
    members = [MemberInfo(A, "Ada", founder_score=45, lab_contributions=labs.get(A, [])),
               MemberInfo(B, "Ben", founder_score=40, lab_contributions=labs.get(B, [])),
               MemberInfo(C, "Cy", founder_score=None)]
    conv = convergence([Member(m.id, m.name) for m in members], [], created, anchor_name="acme",
                       families=["github", "community"], lab=labs)
    facts = [
        EvidenceFact("org_created", "GitHub organization acme created 2026-08-01", source_type="github",
                     event_at=datetime(2026, 8, 1, 12, tzinfo=UTC)),
        EvidenceFact("founding_team", "Two members committed to acme within 30 days of its creation",
                     source_type="github", event_at=datetime(2026, 8, 20, tzinfo=UTC)),
        EvidenceFact("launch", "Show HN: acme", source_type="hackernews", event_at=NOW - timedelta(days=5)),
    ]
    if with_lab:
        facts.append(EvidenceFact("lab_alumni", "2 lab alumni committing to acme", source_type="github",
                                  event_at=datetime(2026, 8, 20, tzinfo=UTC)))
    return CandidateInput(
        kind="team", anchor_type="company", anchor_id=uuid.UUID(int=60), anchor_name="acme", anchor_slug="acme",
        created=created, first_seen_at=NOW - timedelta(days=20), members=members,
        metrics={"commits_4w": 40.0, "stars_30d": 50.0, "new_contributors_30d": 1.0, "releases_90d": 1.0},
        facts=facts, signals=[sig("hn_show", "hackernews", 5)], attention=None, thesis_fit=0.8, theme="ai",
        convergence=conv)


def test_verified_lab_alumni_team_can_reach_emerging_but_not_high():
    with_lab = score_candidate(_talent_team(True), cohort(), TODAY)
    without = score_candidate(_talent_team(False), cohort(), TODAY)
    assert without.tier == "early"
    assert with_lab.tier == "emerging" and with_lab.label == LABEL_TEAM
    assert 50 <= with_lab.score < 70 and with_lab.families == ["community", "github"]
    conv = with_lab.components["team_convergence"]["explanation"]
    assert "Two of three members previously contributed to NVIDIA/cutlass (verified commits, 2024–2025)" in conv
    assert "lab alumni committing together" in with_lab.components["formation_evidence"]["explanation"]
    assert "founded" not in json.dumps(with_lab.components).lower()


def test_talent_person_with_only_github_evidence_stays_early():
    labs = _labs(("openai", "triton", 300, 1, "2024-01-01", "2026-01-01"))
    p = CandidateInput(
        kind="person", anchor_type="person", anchor_id=A, anchor_name="Ada", anchor_slug="ada", created=None,
        first_seen_at=NOW - timedelta(days=3),
        members=[MemberInfo(A, "Ada", founder_score=80, lab_contributions=labs)],
        metrics={"commits_30d": 300.0, "stars_30d": 300.0},
        facts=[EvidenceFact("new_project", "ada created ada/kernels", source_type="github",
                            event_at=NOW - timedelta(days=10)),
               EvidenceFact("young_org", "ada joined acme-labs", source_type="github",
                            event_at=NOW - timedelta(days=8))],
        signals=[], attention=None, thesis_fit=0.9)
    sc = score_candidate(p, build_tech_cohort([]), TODAY)
    assert sc.families == ["github"] and sc.tier in ("early", None) and sc.label in (LABEL_PERSON, None)
    assert "new repository of their own" in sc.components["formation_evidence"]["explanation"]

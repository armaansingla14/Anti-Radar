from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from radar.scoring.cohort import Cohort
from radar.scoring.features import EntityFeatures, PersonFeatures, SignalLite
from radar.scoring.founder import FOUNDER_MAX, founder_score, founder_signal, person_row, research
from radar.scoring.novelty import jaccard, novelty, tokenize
from radar.scoring.thesis_fit import thesis_fit
from radar.thesis.loader import get_thesis

TODAY = date(2026, 10, 6)


@pytest.fixture(scope="module")
def thesis():
    return get_thesis()


# ── thesis fit ───────────────────────────────────────────────────────────────


def test_thesis_fit_stage_multiplier(thesis):
    themes, topics = {"robotics": 1.0}, {"robotics.tactile": 1.0}
    ev = {"robotics.tactile": ["tactile", "gelsight"]}
    pre = thesis_fit(themes, topics, ev, thesis, {}, "pre_seed")
    growth = thesis_fit(themes, topics, ev, thesis, {}, "growth")
    assert pre.value == 15
    assert growth.value == pytest.approx(15 * thesis.stages["growth"])
    assert "Robotics / Tactile sensing" in pre.explanation and "gelsight" in pre.explanation


def test_thesis_fit_theme_weight_and_pref(thesis):
    sw = thesis_fit({"software": 1.0}, {}, {}, thesis, {}, "seed")
    assert sw.value == pytest.approx(15 * thesis.themes["software"].weight * thesis.stages["seed"])
    boosted = thesis_fit({"software": 1.0}, {}, {}, thesis, {"thesis:software": 1.2}, "seed")
    assert boosted.value > sw.value
    capped = thesis_fit({"robotics": 1.0}, {}, {}, thesis, {"thesis:robotics": 1.2}, "formation")
    assert capped.value == 15


def test_thesis_fit_no_match(thesis):
    c = thesis_fit({}, {}, {}, thesis, {}, "seed")
    assert c.value == 0 and "No thesis topic" in c.explanation


# ── novelty ──────────────────────────────────────────────────────────────────


def test_tokenize_and_jaccard():
    a = tokenize("Tactile sensing skin for dexterous robot hands")
    b = tokenize("Dexterous robot hands with tactile skin")
    assert "the" not in a and "tactile" in a
    assert 0.5 < jaccard(a, b) <= 1
    assert jaccard(set(), b) == 0


def test_novelty_recency_and_similarity():
    fresh = datetime(2026, 10, 5, tzinfo=UTC)
    old = fresh - timedelta(days=200)
    toks = tokenize("plasma confinement stellarator control")
    unique_new = novelty(toks, fresh, [("x", tokenize("gpu kernel compiler"))], TODAY, 45)
    dup_new = novelty(toks, fresh, [("Twin", set(toks))], TODAY, 45)
    unique_old = novelty(toks, old, [], TODAY, 45)
    assert unique_new.value > unique_old.value
    assert unique_new.value > dup_new.value
    assert "Twin" in dup_new.explanation
    assert 0 <= dup_new.value <= 10 and unique_new.value <= 10


def test_novelty_without_text_is_neutral():
    c = novelty(set(), datetime(2026, 10, 6, tzinfo=UTC), [], TODAY, 45)
    assert c.value == pytest.approx(10 * (0.5 * 1 + 0.5 * 0.5))


# ── founder ──────────────────────────────────────────────────────────────────


def person(**kw) -> PersonFeatures:
    return PersonFeatures(person_id=uuid.uuid4(), name="P", **kw)


def people_cohort(people):
    c = Cohort.build((person_row(p, TODAY) for p in people), cohort_min=12)
    c.values["tech_output"] = sorted(0.1 * i for i in range(10))
    return c


def test_founder_components_sum_to_at_most_100(thesis):
    assert sum(FOUNDER_MAX.values()) == 100
    sig = SignalLite(
        uuid.uuid4(),
        "affiliation_changed",
        "github",
        "Left NVIDIA",
        datetime(2026, 10, 1, tzinfo=UTC),
        1.0,
        0.9,
    )
    strong = person(
        owned_repo_stars=5000.0,
        recent_repo_stars_30d=900.0,
        commits_30d=300.0,
        followers=50.0,
        papers_24mo=6,
        papers_with_code=6,
        affiliations=["NVIDIA"],
        tier=1.0,
        tier_match="NVIDIA",
        recent_transition=True,
        transition_evidence=[sig.id],
        joined_young_org=True,
        young_org_names=["Acme"],
        theme_scores={"robotics": 1.0},
        signals=[sig],
        own_signals=[sig],
    )
    weak = person()
    c = people_cohort([strong, weak])
    fs = founder_score(strong, c, thesis, {}, TODAY)
    total = sum(x.value for x in fs.components)
    assert 60 < total <= 100
    for comp in fs.components:
        assert 0 <= comp.value <= comp.max
    assert next(x for x in fs.components if x.key == "pedigree").value == 15
    form = next(x for x in fs.components if x.key == "formation")
    assert form.evidence and form.evidence[0][0].id == sig.id
    weak_total = sum(x.value for x in founder_score(weak, c, thesis, {}, TODAY).components)
    assert weak_total < 10


def test_research_saturates_and_rewards_code():
    few = research(person(papers_24mo=1))
    many = research(person(papers_24mo=30))
    with_code = research(person(papers_24mo=30, papers_with_code=30))
    assert few.value < many.value < with_code.value <= 20
    assert research(person()).value == 0


def test_company_founder_signal_weighting():
    none = founder_signal(EntityFeatures(entity_id=uuid.uuid4(), name="c"))
    assert none.value == 0 and "No founders identified yet" in none.explanation
    ids = [uuid.uuid4() for _ in range(3)]
    one = founder_signal(
        EntityFeatures(entity_id=uuid.uuid4(), name="c", founder_scores=[80.0], founder_ids=ids[:1])
    )
    assert one.value == pytest.approx(16)
    three = founder_signal(
        EntityFeatures(entity_id=uuid.uuid4(), name="c", founder_scores=[20.0, 80.0, 40.0], founder_ids=ids)
    )
    assert three.value == pytest.approx(20 * (0.6 * 0.8 + 0.2 * 0.2 + 0.2 * 0.4))
    assert three.raw["founders"][0]["person_id"] == str(ids[1])


def test_formation_ignores_signals_not_about_the_person(thesis):
    """#1: a co-contributor's bio change / a repo measurement linked via a shared repo is not this
    person's formation evidence."""
    other_bio = SignalLite(uuid.uuid4(), "bio_changed", "github", "Mira's bio changed",
                           datetime(2026, 10, 1, tzinfo=UTC), 1.0, 0.9, entity_id=uuid.uuid4())
    repo_measure = SignalLite(uuid.uuid4(), "new_contributors", "github", "repo: 14 new contributors",
                              datetime(2026, 10, 5, tzinfo=UTC), 1.0, 0.9, entity_id=uuid.uuid4())
    p = person(signals=[other_bio, repo_measure], own_signals=[])
    c = people_cohort([p])
    form = next(x for x in founder_score(p, c, thesis, {}, TODAY).components if x.key == "formation")
    assert form.value == 0 and form.evidence == [] and form.raw["collision"] == 0
    assert "transition" not in form.explanation.lower()


def test_founder_signal_does_not_call_contributors_founders():
    """#7: contributor-only links get no founder credit and are not described as founders."""
    f = EntityFeatures(entity_id=uuid.uuid4(), name="c", linked_people=19)
    comp = founder_signal(f)
    assert comp.value == 0 and "linked founder" not in comp.explanation
    assert "19 linked people are not marked as founders" in comp.explanation
    assert comp.raw["linked_people"] == 19


def test_person_attention_unmeasured_awards_no_gap(thesis, monkeypatch):
    """#9: no attention source -> attention None, gap not claimed, 0 points."""
    import radar.scoring.founder as founder_mod

    p = person(owned_repo_stars=5000.0, recent_repo_stars_30d=900.0, commits_30d=300.0)
    c = people_cohort([p])
    real_row = founder_mod.person_row
    monkeypatch.setattr(founder_mod, "person_row",
                        lambda q, t: {**real_row(q, t), "followers": None, "hn_mentions": None})
    fs = founder_score(p, c, thesis, {}, TODAY)
    att = next(x for x in fs.components if x.key == "attention_gap")
    assert fs.attention.attention is None and fs.attention.to_api()["attention"] is None
    assert att.value == 0 and att.raw["gap"] is None and "not measured" in att.explanation
    assert "0th" not in att.explanation


def test_pedigree_from_public_github_org_membership():
    from radar.models.core import Person
    from radar.scoring.extract import person_affiliation_tier, public_org_canonical
    from radar.scoring.founder import pedigree

    thesis = get_thesis()
    assert public_org_canonical({"login": "openai"}) == "OpenAI"
    assert public_org_canonical({"login": "google-deepmind"}) == "Google DeepMind"
    assert public_org_canonical({"login": "tesla-fans-club"}) is None  # partial matches are not trusted
    p = Person(name="Ada", current_company="Acme", prior_affiliations=[],
               attrs={"github_orgs": [{"login": "openai", "name": "openai", "observed_at": "2026-10-06T00:00:00"},
                                      {"login": "random-club"}]})
    aff = person_affiliation_tier(p, thesis)
    assert (aff.tier, aff.tier_match, aff.basis, aff.org_login) == (1.0, "OpenAI", "public_github_org", "openai")
    assert aff.affiliations == ["Acme"] and aff.public_orgs == ["OpenAI"]
    pf = person(affiliations=aff.affiliations, tier=aff.tier, tier_match=aff.tier_match)
    pf.tier_basis, pf.tier_org_login, pf.public_orgs = aff.basis, aff.org_login, aff.public_orgs
    c = pedigree(pf)
    assert c.value == FOUNDER_MAX["pedigree"]
    assert c.explanation == "Public GitHub member of openai (OpenAI, tier-1)."
    assert c.raw["basis"] == "public_github_org" and c.raw["public_github_orgs"] == ["OpenAI"]
    # A self-reported tier-1 affiliation of equal tier: the observed membership is preferred as the basis.
    p2 = Person(name="Ben", current_company="OpenAI", prior_affiliations=[], attrs={"github_orgs": [{"login": "openai"}]})
    assert person_affiliation_tier(p2, thesis).basis == "public_github_org"
    # Without public orgs the old wording stays.
    p3 = Person(name="Cy", current_company="NVIDIA", prior_affiliations=[], attrs={})
    a3 = person_affiliation_tier(p3, thesis)
    assert a3.basis == "affiliation" and pedigree(person(affiliations=a3.affiliations, tier=a3.tier,
                                                         tier_match=a3.tier_match)).explanation.startswith(
        "Affiliation with NVIDIA")

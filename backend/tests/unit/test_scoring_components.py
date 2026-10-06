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

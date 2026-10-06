"""Deterministic natural-language search parser (no DB, no LLM)."""

from __future__ import annotations

import pytest

from radar.schemas.api import SearchFilters
from radar.services import search as S


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    class _Null:
        available = False

    monkeypatch.setattr("radar.llm.provider.get_llm", lambda: _Null())


def parse(q: str) -> tuple[SearchFilters, list[str], str]:
    return S.parse_query(q)


def test_robotics_founders_who_left_major_ai_labs(thesis):
    f, chips, parser = parse("Robotics founders who left major AI labs in the last six months")
    assert parser == "deterministic"
    assert f.entity_types == ["person"]
    assert f.themes == ["robotics"]
    assert "ai" not in f.themes  # "major AI labs" is an affiliation cue, not the AI theme
    assert "OpenAI" in f.prior_affiliations and "Anthropic" in f.prior_affiliations
    assert set(thesis.affiliation_tiers["tier_1"]) <= set(f.prior_affiliations)
    assert f.signal_types == ["affiliation_changed"]
    assert f.event_within_days == 180
    assert "left a tier-1 lab" in chips


def test_new_semiconductor_startups_accelerating():
    f, chips, _ = parse("New semiconductor startups with accelerating GitHub activity")
    assert f.entity_types == ["company"]
    assert f.themes == ["semiconductors"]
    assert f.accelerating is True
    assert f.first_seen_within_days == 30
    assert "accelerating" in chips


def test_inference_outside_nvidia_cuda():
    f, _, _ = parse("Companies working on inference outside NVIDIA CUDA")
    assert f.entity_types == ["company"]
    assert set(f.topics) == {"semiconductors.cuda_alternatives", "ai.inference"}
    assert f.prior_affiliations == []  # NVIDIA here is not an affiliation


def test_interesting_founders_in_autonomous_defense():
    f, _, _ = parse("Interesting founders in autonomous defense")
    assert f.entity_types == ["person"]
    assert f.topics == ["defense.autonomy"]
    assert f.themes == []


def test_research_papers_that_could_become_companies():
    f, chips, _ = parse("Research papers that could become companies")
    assert f.entity_types == ["paper"]
    assert any("spin-out" in c for c in chips)
    assert f.topics == [] and f.themes == []


def test_energy_companies_hiring_ml_engineers():
    f, chips, _ = parse("Energy companies hiring machine learning engineers")
    assert f.entity_types == ["company"]
    assert f.themes == ["energy"]
    assert f.hiring_role_classes == ["ml_research"]
    assert "ai" not in f.themes  # "machine learning" belongs to the hiring clause
    assert "hiring: ml_research" in chips


def test_high_momentum_low_attention():
    f, _, _ = parse("Companies with high technical momentum but low social attention")
    assert f.entity_types == ["company"]
    assert f.sort == "momentum"
    assert f.max_attention == 40


@pytest.mark.parametrize(
    ("query", "check"),
    [
        ("tactile robotics", lambda f: f.topics == ["robotics.tactile"] and f.themes == []),
        ("founders who left Coriolis Aerospace",
         lambda f: f.prior_affiliations == ["Coriolis Aerospace"] and f.signal_types == ["affiliation_changed"]),
        ("former OpenAI researchers", lambda f: f.prior_affiliations == ["OpenAI"] and f.signal_types == []),
        ("ex-Acme Rocketry engineers", lambda f: f.prior_affiliations == ["Acme Rocketry"]),
        ("robotics companies this week", lambda f: f.event_within_days == 7),
        ("defense startups in the past 30 days", lambda f: f.event_within_days == 30),
        ("stealth robotics companies", lambda f: set(f.stage) == {"formation", "pre_seed"}),
        ("pre-seed fusion companies", lambda f: f.stage == ["pre_seed"] and f.topics == ["energy.fusion"]),
        ("under the radar drone companies", lambda f: f.max_attention == 40 and f.topics == ["robotics.drones"]),
        ("companies hiring RF engineers", lambda f: f.hiring_role_classes == ["rf_defense"]),
        ("halcyon", lambda f: f.keywords == ["halcyon"]),
    ],
)
def test_more_cues(query, check):
    f, _, _ = parse(query)
    assert check(f), f.model_dump(exclude_defaults=True)


def test_hybrid_merge_when_llm_available(monkeypatch):
    class _Live:
        available = True

    monkeypatch.setattr("radar.llm.provider.get_llm", lambda: _Live())
    monkeypatch.setattr("radar.llm.search_parse.llm_parse_query",
                        lambda q: SearchFilters(themes=["robotics"], stage=["seed"], sort="delta"))
    f, chips, parser = S.parse_query("Interesting founders in autonomous defense")
    assert parser == "hybrid"
    assert f.topics == ["defense.autonomy"]  # deterministic result kept
    assert f.themes == ["robotics"] and f.stage == ["seed"]
    assert any("(model)" in c for c in chips)


def test_llm_not_called_with_enough_constraints(monkeypatch):
    def boom(_q):
        raise AssertionError("LLM must not be consulted")

    monkeypatch.setattr("radar.llm.search_parse.llm_parse_query", boom)
    _, _, parser = S.parse_query("Energy companies hiring machine learning engineers")
    assert parser == "deterministic"


def test_empty_and_garbage_queries_are_safe():
    f, chips, _ = parse("   ")
    assert f == SearchFilters() and chips == []
    f, _, _ = parse("((( ]]] ***" * 50)
    assert isinstance(f, SearchFilters)

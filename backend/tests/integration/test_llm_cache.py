"""LLM cache: hit path without a live provider, logging, export/import, and cached narratives."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel
from sqlalchemy import func, select

from radar.llm import cache as llm_cache
from radar.llm import narratives
from radar.llm.provider import BaseProvider, LLMUnavailable, NullProvider, Usage, get_llm
from radar.llm.search_parse import llm_parse_query, llm_refine_topics
from radar.models import Company, LLMCache, LLMCall, Signal
from radar.settings import get_settings


class Echo(BaseModel):
    answer: str


class FakeLive(BaseProvider):
    """A live provider whose outputs are scripted per schema; counts calls."""

    name = "anthropic"
    available = True

    def __init__(self, outputs: dict[type, BaseModel]) -> None:
        self.outputs = outputs
        self.calls = 0

    def model_for(self, tier):
        from radar.llm.provider import anthropic_model_for

        return anthropic_model_for(tier)

    def _call_structured(self, model, tier, system, user, schema, max_tokens):
        self.calls += 1
        self.last_user = user
        return self.outputs[schema], Usage(input_tokens=1000, output_tokens=100)


def test_null_provider_without_cache_raises(db):
    assert isinstance(get_llm(), NullProvider)  # LLM_PROVIDER=none in tests
    with pytest.raises(LLMUnavailable):
        NullProvider().structured("t.echo", "fast", "sys", "hello", Echo)


def test_live_call_is_logged_cached_then_served_by_null_provider(db):
    live = FakeLive({Echo: Echo(answer="42")})
    r1 = live.structured("t.echo", "fast", "sys", "hello", Echo)
    assert (r1.parsed.answer, r1.cached, live.calls) == ("42", False, 1)
    assert r1.cost_usd == pytest.approx((1000 * 1 + 100 * 5) / 1e6)

    r2 = live.structured("t.echo", "fast", "sys", "hello", Echo)
    assert r2.cached and live.calls == 1 and r2.cost_usd == 0.0

    # The demo path: no API key at all, same request -> served from cache.
    r3 = NullProvider().structured("t.echo", "fast", "sys", "hello", Echo)
    assert r3.cached and r3.parsed.answer == "42" and r3.model == get_settings().llm_fast_model

    # Different prompt version or input is a miss.
    with pytest.raises(LLMUnavailable):
        NullProvider().structured("t.echo", "fast", "sys", "hello", Echo, prompt_version="v2")
    with pytest.raises(LLMUnavailable):
        NullProvider().structured("t.echo", "fast", "sys", "hello!", Echo)

    db.expire_all()
    rows = db.scalars(select(LLMCall).order_by(LLMCall.created_at)).all()
    assert [r.cache_hit for r in rows] == [False, True, True]
    assert rows[0].input_tokens == 1000 and rows[0].cost_usd > 0 and rows[0].ok
    assert rows[2].provider == "none"


def test_cache_disabled_skips_lookup_and_store(db):
    live = FakeLive({Echo: Echo(answer="x")})
    live.structured("t.nocache", "fast", "sys", "u", Echo, cache=False)
    live.structured("t.nocache", "fast", "sys", "u", Echo, cache=False)
    assert live.calls == 2
    assert db.scalar(select(func.count()).select_from(LLMCache)) == 0


def test_corrupt_cache_entry_is_treated_as_miss(db):
    key = llm_cache.cache_key(get_settings().llm_fast_model, "t.bad", "v1",
                              {"system": "s", "user": "u", "schema": Echo.model_json_schema()})
    llm_cache.cache_put(key, "t.bad", get_settings().llm_fast_model, {"unexpected": 1})
    with pytest.raises(LLMUnavailable):
        NullProvider().structured("t.bad", "fast", "s", "u", Echo)


def test_export_import_roundtrip(db, tmp_path):
    FakeLive({Echo: Echo(answer="a")}).structured("t.one", "fast", "s", "u1", Echo)
    FakeLive({Echo: Echo(answer="b")}).structured("t.two", "fast", "s", "u2", Echo)
    path = tmp_path / "cache.jsonl"
    assert llm_cache.export_cache(path) == 2
    assert llm_cache.export_cache(tmp_path / "one.jsonl", purposes=["t.one"]) == 1
    db.execute(LLMCache.__table__.delete())
    db.commit()
    with pytest.raises(LLMUnavailable):
        NullProvider().structured("t.one", "fast", "s", "u1", Echo)
    path.write_text(path.read_text() + "not json\n")
    assert llm_cache.import_cache(path) == 2
    assert NullProvider().structured("t.two", "fast", "s", "u2", Echo).parsed.answer == "b"


def _company_with_signals(db) -> Company:
    c = Company(slug="acme-robotics", name="Acme Robotics", description="Tactile grippers.", stage="pre_seed",
                thesis_tags=["robotics.tactile"])
    db.add(c)
    db.flush()
    for i, (fam, title, day) in enumerate([("github", "acme/grip crossed 1,000 stars", 20),
                                           ("arxiv", "Paper: GelGrip tactile sensing", 25),
                                           ("jobs", "Hiring a founding controls engineer", 2)]):
        db.add(Signal(entity_id=c.id, entity_type="company", signal_type=f"t{i}", source_type=fam,
                      source_url=f"https://example.com/{i}", title=title, dedup_key=uuid.uuid4().hex,
                      event_at=datetime(2026, 9 if day > 5 else 10, day, tzinfo=UTC)))
    db.flush()
    return c


def test_why_now_llm_then_cached_and_citations_mapped(db):
    c = _company_with_signals(db)
    live = FakeLive({narratives._NarrOut: narratives._NarrOut(
        text="GitHub, arXiv and hiring signals converged in late September.", citations=["G1", "G3", "G9"])})
    n1 = narratives.why_now(db, c, provider=live)
    sig_ids = {str(s.id) for s in db.scalars(select(Signal))}
    assert n1["generator"] == "llm" and n1["epistemic"] == "MODEL_OPINION"
    assert len(n1["citations"]) == 2 and set(n1["citations"]) <= sig_ids  # G9 dropped
    assert '<source id="G1"' in live.last_user

    n2 = narratives.why_now(db, c, provider=NullProvider())
    assert n2["generator"] == "cached" and n2["text"] == n1["text"]


def test_why_now_ungrounded_llm_output_falls_back_to_template(db):
    c = _company_with_signals(db)
    live = FakeLive({narratives._NarrOut: narratives._NarrOut(text="Hot company!", citations=["S1"])})
    n = narratives.why_now(db, c, provider=live)
    assert n["generator"] == "template" and n["epistemic"] == "INFERENCE"
    assert n["text"].startswith("Three independent signals appeared within the past 16 days:")
    assert len(n["citations"]) == 3


def test_risks_backfill_required_skeptical_categories(db):
    c = _company_with_signals(db)
    out = narratives._RiskOut(items=[
        narratives._RiskItem(text="Can they ship hardware at volume?", category="technical", citations=[]),
        narratives._RiskItem(text="What stops Robotiq copying this?", category="defensibility", citations=["G2"]),
    ])
    items = narratives.risks_questions(db, c, provider=FakeLive({narratives._RiskOut: out}))
    texts = " ".join(i["text"] for i in items).lower()
    assert items[0]["generator"] == "llm"
    assert "full-time" in texts and "research" in texts and "customer" in texts and "incumbents" in texts
    assert sum("defensib" in i["text"].lower() or "reproducing" in i["text"].lower() for i in items) <= 1


def test_template_narratives_without_llm(db, thesis):
    c = _company_with_signals(db)
    anti = narratives.why_anti(db, c, thesis, provider=NullProvider())
    assert anti["generator"] == "template" and "Robotics → Tactile sensing" in anti["text"]
    risks = narratives.risks_questions(db, c, provider=NullProvider())
    assert len(risks) >= 5 and all(r["generator"] == "template" for r in risks)
    mb = narratives.morning_brief_summary([{"n": 1, "text": "Acme crossed 1k stars.", "evidence": ["sig-1"]}],
                                          provider=NullProvider())
    assert mb["citations"] == ["sig-1"] and mb["text"].startswith("1 notable change")


def test_search_parse_filters_to_thesis_values(db):
    from radar.llm.search_parse import _ParsedQuery, _TopicOut, _TopicScore

    parsed = _ParsedQuery(
        entity_types=["person"], themes=["robotics", "crypto"], topics=["robotics.tactile", "robotics.made_up"],
        keywords=["gripper"], prior_affiliations=["Tesla"], hiring_role_classes=["robotics_controls", "chef"],
        stage=["seed", "series_z"], min_score=-1, first_seen_within_days=30, event_within_days=-1,
        accelerating=True, sort="momentum",
    )
    f = llm_parse_query("ex-Tesla tactile robotics founders, new this month", provider=FakeLive({_ParsedQuery: parsed}))
    assert f.themes == ["robotics"] and f.topics == ["robotics.tactile"]
    assert f.hiring_role_classes == ["robotics_controls"] and f.stage == ["seed"]
    assert f.min_score is None and f.first_seen_within_days == 30 and f.event_within_days is None
    assert llm_parse_query("anything else", provider=NullProvider()) is None

    topics = _TopicOut(topics=[_TopicScore(key="robotics.tactile", score=1.7), _TopicScore(key="ai.agents", score=.9)])
    got = llm_refine_topics("GelSight-style sensor", ["robotics.tactile", "robotics.manipulation"],
                            provider=FakeLive({_TopicOut: topics}))
    assert got == [("robotics.tactile", 1.0)]
    assert llm_refine_topics("other text", ["robotics.tactile"], provider=NullProvider()) is None

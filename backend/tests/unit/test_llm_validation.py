"""Citation validation, brief/memo schemas, cost and cache-key logic (no database)."""

from __future__ import annotations

import pytest
from anthropic.lib._parse._transform import transform_schema
from pydantic import ValidationError

from radar.agent.brief_schema import AntiBrief, AntiMemo, AntiMemoDraft, Claim
from radar.agent.render import brief_to_markdown, memo_to_markdown
from radar.llm.cache import cache_key
from radar.llm.cost import cost_usd, price_for
from radar.llm.guard import validate_citations


def _brief(**over) -> AntiBrief:
    base = {
        "subject": "Acme Robotics", "date": "2026-10-06", "what_they_do": [], "why_now": [], "what_changed": [], "founders": [],
        "technical_edge": [], "market": [], "competitors": [], "why_this_could_matter": [], "why_this_could_be_wrong": [],
        "contradictory_signals": [], "questions_to_ask_founders": [], "signals": [], "unknowns": [],
    }
    base.update(over)
    return AntiBrief(**base)


def test_unknown_ids_are_stripped_and_unsupported_facts_downgraded():
    brief = _brief(
        what_they_do=[Claim(text="Builds tactile grippers.", epistemic="FACT", source_ids=["S1", "S99"])],
        founders=[Claim(text="Founded by a former DeepMind researcher.", epistemic="FACT", source_ids=["S42"])],
        market=[Claim(text="Large market.", epistemic="MODEL_OPINION", source_ids=[])],
        why_now=[Claim(text="Stars tripled [S2] after the launch [S77].", epistemic="INFERENCE", source_ids=["s2"])],
    )
    rep = validate_citations(brief, {"S1", "S2", "S3"})

    assert brief.what_they_do[0].source_ids == ["S1"]
    assert brief.what_they_do[0].epistemic == "FACT"
    f = brief.founders[0]
    assert f.source_ids == [] and f.epistemic == "INFERENCE" and f.text.endswith("(unverified)")
    assert brief.market[0].epistemic == "MODEL_OPINION"  # opinions may stand without sources
    assert brief.why_now[0].source_ids == ["S2"]  # normalised from 's2'
    assert "[S77]" not in brief.why_now[0].text and "[S2]" in brief.why_now[0].text
    assert set(rep.invalid_ids) == {"S99", "S42", "S77"}
    assert rep.downgraded == 1 and rep.total_claims == 4
    assert rep.ok_ratio == pytest.approx(1 / 4)  # only the MODEL_OPINION claim needed no correction


def test_validation_is_idempotent_and_dedupes():
    brief = _brief(signals=[Claim(text="x", epistemic="FACT", source_ids=["S1", "[S1]", " s1 "])])
    validate_citations(brief, ["S1"])
    assert brief.signals[0].source_ids == ["S1"]
    rep = validate_citations(brief, ["S1"])
    assert rep.ok_ratio == 1.0 and not rep.invalid_ids


def test_downgrade_does_not_duplicate_suffix():
    brief = _brief(signals=[Claim(text="x", epistemic="FACT", source_ids=["S9"])])
    validate_citations(brief, set())
    brief.signals[0].epistemic = "FACT"
    validate_citations(brief, set())
    assert brief.signals[0].text == "x (unverified)"


def test_validates_memo_claims_too():
    memo = AntiMemoDraft(
        company="A", one_liner="b", why_on_radar=[Claim(text="t", epistemic="FACT", source_ids=["S5"])], why_now=[],
        founders=[], technical_insight=[], what_others_may_be_missing=[], market=[], competition=[], evidence=[],
        risks=[], questions_to_answer=[],
    )
    rep = validate_citations(memo, {"S1"})
    assert rep.downgraded == 1 and memo.why_on_radar[0].epistemic == "INFERENCE"


@pytest.mark.parametrize(
    "claim",
    [
        {"text": "x", "epistemic": "TRUTH", "source_ids": []},
        {"text": "x", "epistemic": "FACT"},
        {"text": "x", "epistemic": "FACT", "source_ids": "S1"},
        {"text": "   ", "epistemic": "FACT", "source_ids": []},
        {"text": "x", "epistemic": "FACT", "source_ids": [], "confidence": 0.9},
    ],
)
def test_claim_rejects_malformed(claim):
    with pytest.raises(ValidationError):
        Claim.model_validate(claim)


def test_brief_rejects_malformed_structures():
    good = _brief().model_dump()
    for broken in (
        {k: v for k, v in good.items() if k != "why_now"},
        {**good, "why_now": "Recent momentum"},
        {**good, "questions_to_ask_founders": [{"q": "?"}]},
        {**good, "score": 100},
    ):
        with pytest.raises(ValidationError):
            AntiBrief.model_validate(broken)


@pytest.mark.parametrize("model", [AntiBrief, AntiMemoDraft])
def test_schemas_are_structured_output_compatible(model):
    schema = model.model_json_schema()
    assert set(schema["required"]) == set(schema["properties"])
    claim = schema["$defs"]["Claim"]
    assert set(claim["required"]) == {"text", "epistemic", "source_ids"}
    strict = transform_schema(model)
    assert strict["additionalProperties"] is False
    assert strict["$defs"]["Claim"]["properties"]["epistemic"]["enum"] == ["FACT", "INFERENCE", "MODEL_OPINION"]


def test_memo_score_summary_not_in_llm_schema():
    assert "anti_score_summary" not in AntiMemoDraft.model_fields
    assert "anti_score_summary" in AntiMemo.model_fields


def test_cost_table_and_fallbacks():
    assert cost_usd("claude-opus-5-5", 1_000_000, 100_000) == pytest.approx(4.0 + 2.0)
    assert cost_usd("claude-haiku-4-5", 2000, 500) == pytest.approx(0.0045)
    assert cost_usd("claude-sonnet-5-5", 0, 0, cache_read_tokens=1_000_000) == pytest.approx(0.2)
    assert price_for("gpt-5-mini").approximate and not price_for("claude-sonnet-5-5").approximate
    assert price_for("claude-opus-5-5-preview") is price_for("claude-opus-5-5")
    assert cost_usd("unknown-model", 10_000, 10_000) == 0.0


def test_cache_key_is_stable_and_sensitive():
    a = cache_key("m", "p", "v1", {"user": "hi", "schema": {"b": 1, "a": [1, 2]}})
    b = cache_key("m", "p", "v1", {"schema": {"a": [1, 2], "b": 1}, "user": "hi"})
    assert a == b and len(a) == 64
    assert a != cache_key("m", "p", "v2", {"user": "hi", "schema": {"b": 1, "a": [1, 2]}})
    assert a != cache_key("m2", "p", "v1", {"user": "hi", "schema": {"b": 1, "a": [1, 2]}})
    assert a != cache_key("m", "p", "v1", {"user": "hi ", "schema": {"b": 1, "a": [1, 2]}})


def test_markdown_renders_citations_labels_and_unknowns():
    brief = _brief(
        what_they_do=[Claim(text="Builds grippers.", epistemic="FACT", source_ids=["S1"])],
        market=[Claim(text="Could sell to 3PLs.", epistemic="MODEL_OPINION", source_ids=[])],
    )
    sources = [{"id": "S1", "url": "https://acme.ai", "title": "Acme site", "source_type": "website",
                "fetched_at": "2026-10-06T12:00:00+00:00"}]
    md = brief_to_markdown(brief, sources, generator="llm", model="claude-opus-5-5")
    assert "- Builds grippers. [S1]" in md
    assert "Could sell to 3PLs. _(MODEL_OPINION)_" in md
    assert "## Competitors\n\n_Unknown — no evidence collected._" in md
    assert "**[S1]** Acme site — https://acme.ai (website, 2026-10-06)" in md
    memo = AntiMemo(company="Acme", one_liner="Grippers.", why_on_radar=[], why_now=[], founders=[],
                    technical_insight=[], what_others_may_be_missing=[], market=[], competition=[], evidence=[],
                    risks=[], questions_to_answer=["Is it defensible?"], anti_score_summary="Anti Score 70/100.")
    mmd = memo_to_markdown(memo, sources)
    assert "Anti Score 70/100." in mmd and "- Is it defensible?" in mmd and "deterministic template" in mmd

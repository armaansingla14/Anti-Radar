"""Model comparison: normalization math, the recommendation rule, insufficient data, configured models."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from radar.evals.compare import configured_models, normalize, pinned_provider, recommend_models
from radar.llm.provider import BaseProvider, Usage
from radar.models import EvalCaseResult


def R(model: str, passed: bool, *, provider: str = "anthropic", task: str = "meeting_brief_template",  # noqa: N802
      cost: float = 0.0, latency: float | None = 100.0, claims: int = 10, unsupported: int = 0,
      cv: float | None = 1.0, cov: float | None = 0.5, sv: bool | None = True) -> EvalCaseResult:
    return EvalCaseResult(batch_id=uuid.uuid4(), case_id=f"c-{uuid.uuid4().hex[:6]}", agent="MEETING BRIEF", task=task,
                          provider=provider, model=model, passed=passed, latency_ms=latency, cost_usd=cost,
                          metrics={"claims": claims, "unsupported_claims": unsupported, "citation_validity": cv,
                                   "coverage": cov, "structured_valid": sv})


def many(model, n, n_pass, **kw):
    return [R(model, i < n_pass, **kw) for i in range(n)]


def test_normalize_math():
    rows = [R("a", True, cost=0.01, latency=100, claims=10, unsupported=1, cv=1.0, cov=0.4),
            R("a", False, cost=0.03, latency=300, claims=10, unsupported=0, cv=0.5, cov=None, sv=False),
            R("a", True, cost=0.02, latency=200, claims=0, unsupported=0, cv=None, cov=0.6)]
    (n,) = normalize(rows)
    assert n["n"] == 3 and n["pass_rate"]["value"] == pytest.approx(2 / 3, abs=1e-4)
    assert n["citation_validity"] == {"value": 0.75, "n": 2, "reason": None}
    assert n["unsupported_claim_rate"]["value"] == 0.05 and n["unsupported_claim_rate"]["n"] == 20
    assert n["coverage"]["value"] == 0.5 and n["latency_p50_ms"]["value"] == 200.0
    assert n["cost_per_case"]["value"] == 0.02 and n["structured_compliance"]["value"] == pytest.approx(2 / 3, abs=1e-4)


def test_recommends_the_cheapest_model_within_the_quality_floor():
    rows = (many("big", 12, 12, cost=0.09, unsupported=0) + many("small", 12, 12, cost=0.012, unsupported=0)
            + many("template", 12, 9, provider="template", cost=0.0))
    (rec,) = recommend_models(None, results=rows, min_cases=10)
    assert rec["recommendation"] == {"provider": "anthropic", "model": "small"}
    assert "small: pass 1.00 (n=12) vs best 1.00" in rec["reason"] and "$0.012/case" in rec["reason"]


def test_template_is_a_legitimate_recommendation():
    rows = many("big", 12, 12, cost=0.09) + many("template", 12, 12, provider="template", cost=0.0)
    (rec,) = recommend_models(None, results=rows, min_cases=10)
    assert rec["recommendation"] == {"provider": "template", "model": "template"}


def test_unsupported_rate_floor_excludes_a_cheaper_model():
    rows = many("big", 12, 12, cost=0.09, unsupported=0) + many("cheap", 12, 12, cost=0.001, unsupported=3)
    (rec,) = recommend_models(None, results=rows, min_cases=10)
    assert rec["recommendation"]["model"] == "big"


def test_insufficient_data():
    (rec,) = recommend_models(None, results=many("big", 12, 12), min_cases=10)
    assert rec["recommendation"] is None and rec["reason"].startswith("insufficient evaluation data: 1 model(s)")
    (rec,) = recommend_models(None, results=many("big", 12, 12) + many("small", 4, 4), min_cases=10)
    assert rec["recommendation"] is None and "small=4" in rec["reason"]


def test_deterministic_tasks_get_no_recommendation():
    assert recommend_models(None, results=many("template", 12, 12, provider="template", task="claim_checks")) == []


def _settings(**kw):
    base = dict(anthropic_api_key=None, openai_api_key=None, eval_providers="", llm_fast_model="claude-haiku-4-5",
                llm_standard_model="claude-sonnet-5-5", llm_deep_model="claude-opus-5-5",
                openai_fast_model="gpt-5-mini", openai_deep_model="gpt-5")
    return SimpleNamespace(**{**base, **kw})


def test_configured_models_only_with_keys(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    assert configured_models(_settings(eval_providers="anthropic,openai")) == []
    got = configured_models(_settings(openai_api_key="sk-test", eval_providers="openai"))
    assert [(m.provider, m.model) for m in got] == [("openai", "gpt-5-mini"), ("openai", "gpt-5")]
    got = configured_models(_settings(anthropic_api_key="k", llm_standard_model="claude-opus-5-5",
                                      eval_providers="anthropic"))
    assert [(m.model, m.tiers) for m in got] == [("claude-haiku-4-5", ("fast",)),
                                                ("claude-opus-5-5", ("standard", "deep"))]


def test_keys_alone_never_opt_a_provider_in(monkeypatch):
    """A key in the environment is not consent: the provider must be named in EVAL_PROVIDERS, and a base-URL
    override (a gateway for some other deployment) disqualifies it."""
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    assert configured_models(_settings(anthropic_api_key="k", openai_api_key="sk")) == []
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://gateway.example.com")
    assert configured_models(_settings(anthropic_api_key="k", eval_providers="anthropic")) == []


def test_pinned_provider_answers_every_tier_with_its_model():
    from radar.evals.compare import ConfiguredModel

    p = pinned_provider(ConfiguredModel("openai", "gpt-5-mini", ("fast",)), _settings(openai_api_key="sk-test"))
    assert p.model_for("deep") == "gpt-5-mini" == p.model_for("fast") and p.available
    with pytest.raises(ValueError):
        pinned_provider(ConfiguredModel("openai", "gpt-5", ("deep",)), _settings())


class Scripted(BaseProvider):
    """A fake provider pinned to a model name; never touches a network."""

    name = "anthropic"
    available = True

    def __init__(self, model: str) -> None:
        self.model = model

    def model_for(self, tier):
        return self.model

    def _call_structured(self, model, tier, system, user, schema, max_tokens):
        raise AssertionError("not used in unit tests")


def test_provider_label_uses_the_pinned_model():
    from radar.evals.runner import provider_label

    assert provider_label(Scripted("m1")) == ("anthropic", "m1")
    assert provider_label(None) == ("template", "template")
    assert Usage  # imported for parity with the integration fakes

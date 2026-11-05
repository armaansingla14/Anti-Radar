"""Evaluation metrics: rates, nulls with reasons, nearest-rank percentiles, structured validity (no database)."""

from __future__ import annotations

from types import SimpleNamespace

from radar.evals.checks import CheckResult, ClaimRef
from radar.evals.metrics import METHODOLOGY, _counts, metric, nearest_rank, structured_valid, timing_ms


def test_metric_is_null_with_reason_when_not_computable():
    assert metric(None, 0, "no runs") == {"value": None, "n": 0, "reason": "no runs"}
    assert metric(0.5, 0, "no runs") == {"value": None, "n": 0, "reason": "no runs"}
    assert metric(0.0, 3, "x") == {"value": 0.0, "n": 3, "reason": None}
    assert metric(2 / 3, 3, "x")["value"] == 0.6667


def test_nearest_rank_percentiles():
    xs = [100.0, 300.0, 200.0, 400.0, 500.0, 600.0, 700.0, 800.0, 900.0, 1000.0]
    assert nearest_rank(xs, 50) == 500.0 and nearest_rank(xs, 95) == 1000.0
    assert nearest_rank([42.0], 95) == 42.0 and nearest_rank([], 50) is None


def test_timing_only_from_a_timing_entry():
    assert timing_ms([{"type": "validation"}]) is None
    assert timing_ms([{"type": "timing", "ms": 10}, {"type": "x"}, {"type": "timing", "ms": 12.5}]) == 12.5
    assert timing_ms(None) is None


def _res(claims, cited=(), unsupported=(), total=0, invalid=0):
    return CheckResult(claims=claims, cited_ids=set(cited), unsupported=set(unsupported), citations_total=total,
                       invalid_citations=invalid)


def test_counts_rates_and_nulls():
    report = SimpleNamespace(sources=[{"id": "S1"}, {"id": "S2"}, {"id": "S3"}, {"id": "S4"}])
    claims = [ClaimRef("a", "x", "FACT", ["S1"]), ClaimRef("a", "y", "INFERENCE", ["S2"]),
              ClaimRef("b", "z", "FACT", ["S9"]), ClaimRef("b", "w", "MODEL_OPINION", [])]
    c = _counts(report, _res(claims, cited={"S1", "S2"}, unsupported={2}, total=3, invalid=1))
    assert c["claims_total"] == 4 and c["fact_claims"] == 2 and c["unsupported_claim_rate"] == 0.25
    assert c["citation_precision"] == 2 / 3 and c["sources_used"] == 2 and c["source_coverage"] == 0.5
    empty = _counts(SimpleNamespace(sources=[]), _res([]))
    assert empty["unsupported_claim_rate"] is None and empty["citation_precision"] is None
    assert empty["source_coverage"] is None and empty["claims_total"] == 0


def test_structured_valid_per_kind():
    claim = {"text": "t", "epistemic": "FACT", "source_ids": ["S1"]}
    brief = {"subject": "A", "date": "2026-10-06", **{k: [claim] for k in (
        "what_they_do", "why_now", "what_changed", "founders", "technical_edge", "market", "competitors",
        "why_this_could_matter", "why_this_could_be_wrong", "contradictory_signals", "signals")},
        "questions_to_ask_founders": [], "unknowns": []}
    assert structured_valid("brief", brief) == (True, None)
    ok, note = structured_valid("brief", {**brief, "extra": 1})
    assert not ok and "AntiBrief" in note
    meeting = {"name": "A", "date": "2026-10-06", **{k: [] for k in (
        "context_30s", "why_on_radar", "what_they_built", "technical_background", "what_changed", "why_anti_fund",
        "questions", "things_to_verify", "unknowns")}}
    assert structured_valid("meeting_brief", meeting)[0]
    memo = {"company": "A", "one_liner": "x", **{k: [] for k in (
        "why_on_radar", "why_now", "founders", "technical_insight", "what_others_may_be_missing", "market",
        "competition", "evidence", "risks", "questions_to_answer")}, "anti_score_summary": "s"}
    assert structured_valid("memo", memo)[0] and not structured_valid("memo", {"company": "A"})[0]
    assert not structured_valid("morning_brief", {"date": "2026-10-06"})[0]
    assert not structured_valid("nope", {})[0]
    ok, _ = structured_valid("diligence", {"claims": [{"claim_text": "x"}]})
    assert isinstance(ok, bool)


def test_methodology_defines_every_reported_metric():
    for key in ("success_rate", "structured_validity_rate", "error_rate", "fallback_rate", "citation_validity",
                "unsupported_claim_rate", "source_coverage", "avg_sources_used", "latency_p50_ms", "latency_p95_ms",
                "avg_cost_usd", "human_review_pending", "guard_interventions", "llm_telemetry"):
        assert METHODOLOGY[key]
    assert "existence, not semantic support" in METHODOLOGY["citation_validity"]

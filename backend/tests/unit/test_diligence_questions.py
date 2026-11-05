"""Founder questions come only from gaps and conflicts, are specific, carry a reason, and are ordered."""

from __future__ import annotations

from radar.diligence.questions import generate_questions
from tests.fixtures.diligence.helpers import claim

GENERIC = ("moat", "competitive advantage", "tell me about", "vision", "go-to-market")


def _q(claims=(), conflicts=(), oss=None, deps=None, sec=None, ai=None):
    return generate_questions(list(claims), list(conflicts), oss=oss or {}, deps=deps or {}, sec=sec or {}, ai=ai,
                              product="Acme")


def test_no_evidence_gaps_no_questions():
    c = claim("Acme is written in Rust.", "ARCHITECTURE")
    c.status, c.testability = "VERIFIED", "HIGH"
    assert _q([c]) == []


def test_stale_baseline_question_is_specific():
    c = claim("Acme delivers 2.8x the throughput of vLLM 0.8.", "PERFORMANCE")
    c.status, c.testability = "UNVERIFIED", "HIGH"
    conflict = {"id": "C1", "kind": "stale_benchmark_baseline", "label": "CLAIM REQUIRES CLARIFICATION",
                "claim_ordinals": [1], "summary": "Benchmark compares against vLLM 0.8; vLLM 0.11 was released.",
                "evidence": [{"url": "https://pypi.org/project/vllm/", "title": "PyPI vllm", "artifact_key": "pkg:pypi:vllm"}],
                "details": {"baseline": "vLLM", "claimed_version": "0.8", "latest_version": "0.11.0",
                            "latest_date": "2025-10-02"}}
    qs = _q([c], [conflict])
    assert qs[0]["question"] == ("Your benchmark shows 2.8x against vLLM 0.8; vLLM 0.11.0 shipped on 2025-10-02. "
                                 "Have you rerun it, and what changed?")
    assert qs[0]["reason"] and qs[0]["claim_ordinals"] == [1] and qs[0]["evidence"][0]["artifact_key"] == "pkg:pypi:vllm"
    assert len(qs) == 1  # the conflict covers the claim: no second, generic benchmark question


def test_questions_from_gaps_ordered_by_materiality():
    perf = claim("Acme is 3x faster than vLLM on H100.", "PERFORMANCE", ordinal=1)
    perf.status, perf.testability = "UNVERIFIED", "HIGH"
    trac = claim("Trusted by 500+ companies.", "CUSTOMER_TRACTION", ordinal=2)
    trac.status, trac.testability = "UNTESTABLE", "NOT_TESTABLE"
    sec = {"findings": [{"kind": "secret", "material_question": True, "title": "Possible secret pattern",
                         "evidence": {"path": ".env.example", "line": 2, "url": "u", "artifact_key": "k"}}]}
    oss = {"repo": "acme/engine", "risks": [{"id": "single_maintainer", "title": "71% of commits come from one contributor (a)",
                                             "url": "u"}]}
    ai = {"llm_providers": ["OpenAI"], "routers": [], "eval_evidence": []}
    qs = _q([perf, trac], oss=oss, sec=sec, ai=ai)
    texts = [q["question"] for q in qs]
    assert texts[0].startswith("A pattern resembling a credential appears in .env.example line 2")
    assert "exact benchmark configuration" in texts[1] and "3x faster than vLLM" in texts[1]
    assert any("71% of commits" in t for t in texts)
    assert any("Trusted by 500+ companies" in t for t in texts)
    assert any("through OpenAI" in t for t in texts)
    assert all(q["reason"] for q in qs)
    assert not any(g in t.lower() for t in texts for g in GENERIC)
    assert [q["priority"] for q in qs] == sorted((q["priority"] for q in qs), reverse=True)

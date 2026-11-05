"""The full analysis over the fixture capture: schema validation, determinism, markdown, sources."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from radar.diligence.analyze import analyze
from radar.diligence.capture import load
from radar.diligence.report import DiligenceReport, to_markdown
from tests.fixtures.diligence.helpers import SAMPLE_CAPTURE

COMPANY = {"id": None, "slug": "quillserve-fixture", "name": "Quillserve", "website": "https://quillserve.example",
           "github": "quillserve-fixture"}


@pytest.fixture(scope="module")
def analysis():
    cap = load(SAMPLE_CAPTURE)
    return analyze(cap.artifacts, cap.facts, as_of=cap.collected_at, company=COMPANY)


def test_fixture_statuses_cover_the_rules(analysis):
    by_text = {c.text: c for c in analysis.claims}
    assert by_text["Quillserve has over 10k GitHub stars."].status == "CONTRADICTED"
    assert by_text["Quillserve is a high-throughput LLM inference engine written in Rust with custom ROCm and CUDA "
                   "kernels."].status == "VERIFIED"
    assert by_text["Supports NVIDIA H100 and AMD MI300X out of the box."].status == "PARTIALLY_SUPPORTED"
    assert by_text["Quillserve Cloud is SOC 2 Type II certified."].status == "UNTESTABLE"
    assert by_text["Quillserve delivers 3x higher throughput than vLLM 0.8 on AMD MI300X."].status == "UNVERIFIED"
    assert by_text["Quillserve is fully open source under the Apache 2.0 license."].status == "PARTIALLY_SUPPORTED"
    kinds = {c["kind"] for c in analysis.conflicts}
    assert {"stale_benchmark_baseline", "open_source_incomplete", "production_ready_no_stable_release",
            "multi_cloud_single_provider", "public_metric_mismatch"} <= kinds


def test_report_validates_and_is_deterministic(analysis):
    body = analysis.report.model_dump(mode="json")
    DiligenceReport.model_validate(body)
    cap = load(SAMPLE_CAPTURE)
    again = analyze(cap.artifacts, cap.facts, as_of=cap.collected_at, company=COMPANY)
    assert again.report.model_dump(mode="json") == body
    assert again.markdown == analysis.markdown == to_markdown(DiligenceReport.model_validate(body))


def test_schema_forbids_extra_fields(analysis):
    body = analysis.report.model_dump(mode="json")
    body["recommendation"] = "invest"
    with pytest.raises(ValidationError):
        DiligenceReport.model_validate(body)


def test_executive_summary_from_counts_not_a_recommendation(analysis):
    es = analysis.report.executive_summary
    assert es.material_claims == len(analysis.claims) == sum(es.counts.values())
    assert es.evidence_reviewed == 29
    assert "EVIDENCE CONFLICT" in es.largest_uncertainty
    assert es.critical_question == analysis.questions[0]["question"]
    text = (analysis.markdown + es.text).lower()
    for word in ("recommend investing", "we recommend", "buy", "pass on", "strong investment"):
        assert word not in text
    assert "not an investment recommendation" in analysis.report.disclaimer


def test_sources_are_cited_and_resolve(analysis):
    r = analysis.report
    ids = {s.id for s in r.sources}
    assert [s.id for s in r.sources] == [f"S{i}" for i in range(1, len(r.sources) + 1)]
    for c in r.claims:
        assert c.source_id in ids
        for e in c.independent_evidence + c.contradictory_evidence + c.company_evidence:
            assert e.source_id is None or e.source_id in ids
    assert set(r.sources[0].model_dump()) == {"id", "url", "title", "source_type", "publisher", "fetched_at",
                                              "artifact_key"}


def test_markdown_sections(analysis):
    md = analysis.markdown
    for h in ("# Technical Diligence — Quillserve", "## Executive summary", "## Claim ledger", "## Contradictory evidence",
              "## Technical underwriting", "## Security findings", "## Questions for founders", "## Sources"):
        assert h in md
    assert "EVIDENCE CONFLICT" in md and "CONTRADICTED" not in md.split("## Claim ledger")[1].split("###")[0]
    assert "Validation plan** (not executed)" in md


def test_underwriting_has_both_scales_and_no_single_score(analysis):
    cats = {u.key: u for u in analysis.report.underwriting}
    assert cats["CLAIM_VERIFICATION"].scale == "evidence" and cats["SECURITY"].scale == "risk"
    assert cats["OPEN_SOURCE_HEALTH"].rating == "HIGH"
    assert "71% of commits come from one contributor" in cats["OPEN_SOURCE_HEALTH"].headline
    assert "AI_SYSTEMS" in cats
    assert not hasattr(analysis.report, "score")

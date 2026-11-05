"""Report evaluation end to end: real template reports, a hand-built bad report, flag promotion, warm-up."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from radar.agent.analyst import investigate
from radar.agent.meeting import generate_meeting_brief
from radar.agent.memo import generate_memo
from radar.evals.failures import promote_flags
from radar.evals.metrics import agent_metrics, evaluate_all, evaluate_report
from radar.llm.provider import NullProvider
from radar.models import AgentFailure, AgentRunEvaluation, ResearchReport
from tests.integration.test_analyst import acme  # noqa: F401 — fixture reuse
from tests.integration.test_api_meet import builder  # noqa: F401 — fixture reuse


def test_template_brief_evaluation(db, acme):  # noqa: F811
    report = investigate(db, "company", acme.id, live_fetch=False, provider=NullProvider())
    assert report.trace[-1]["type"] == "timing" and report.trace[-1]["ms"] >= 0
    assert report.trace[-2]["type"] == "validation"
    ev = evaluate_report(db, report)
    assert ev.agent == "RADAR ANALYST" and ev.generator == "template" and ev.model is None
    assert ev.latency_ms is not None and ev.fallback is False and ev.error is False
    assert ev.task_success and ev.structured_output_valid
    assert ev.citation_precision == 1.0 and ev.unsupported_claims == 0 and ev.unsupported_claim_rate == 0.0
    assert ev.claims_total > 0 and ev.sources_registered == len(report.sources) and 0 < ev.source_coverage <= 1
    inj = [f for f in ev.flags if f["category"] == "PROMPT_INJECTION_ATTEMPT"]
    assert inj and all(f["severity"] == "INFO" and f["rule"] == "injection_in_sources" for f in inj)
    assert "github.com/acme-robotics/gelgrip" in inj[0]["detail"]
    # Upsert is idempotent: same row, one row.
    again = evaluate_report(db, report)
    assert again.id == ev.id
    assert db.scalar(select(func.count()).select_from(AgentRunEvaluation)) == 1


def test_memo_records_timing_but_not_fallback(db, acme):  # noqa: F811
    memo = generate_memo(db, "company", acme.id, provider=NullProvider())
    assert memo.trace[-1]["type"] == "timing"
    ev = evaluate_report(db, memo)
    assert ev.agent == "ANTI MEMO" and ev.fallback is False and "not measured" in ev.notes


def test_template_meeting_brief_evaluation(db, builder):  # noqa: F811
    rep = generate_meeting_brief(db, builder, provider=NullProvider())
    assert rep.trace[0]["type"] == "validation" and rep.trace[-1]["type"] == "timing"
    ev = evaluate_report(db, rep)
    assert ev.agent == "MEETING BRIEF" and ev.latency_ms is not None
    assert ev.fallback is False  # no LLM configured: nothing was attempted
    assert ev.citation_precision == 1.0 and ev.unsupported_claims == 0
    assert not [f for f in ev.flags if f["rule"] == "llm_fallback"]


def _bad_report(db, entity_id=None):
    claim = lambda t, e, ids: {"text": t, "epistemic": e, "source_ids": ids}  # noqa: E731
    body = {"subject": "Acme", "date": "2026-10-06",
            "what_they_do": [claim("Acme builds grippers.", "FACT", ["S1"])],
            "why_now": [claim("Acme raised a $12M seed round.", "FACT", ["S1"])],
            "what_changed": [claim("Acme shipped v2.", "FACT", [])],
            "founders": [claim("Jane leads Acme.", "FACT", ["S99"])],
            "technical_edge": [], "market": [], "competitors": [], "why_this_could_matter": [],
            "why_this_could_be_wrong": [], "contradictory_signals": [], "questions_to_ask_founders": [],
            "signals": [], "unknowns": []}
    r = ResearchReport(kind="brief", entity_type="company", entity_id=entity_id, title="Anti Brief — Acme",
                       body=body, markdown="", generator="llm", model="scripted", status="complete",
                       sources=[{"id": "S1", "url": "https://github.com/acme/grip", "title": "Acme builds grippers",
                                 "source_type": "github", "fetched_at": "2026-10-05T12:00:00+00:00"},
                                {"id": "S2", "url": "https://acme.example", "title": "Acme site",
                                 "source_type": "website", "fetched_at": "2026-10-05T12:00:00+00:00"}],
                       trace=[{"type": "validation", "total_claims": 4, "invalid_ids": ["S7"], "downgraded": 1,
                               "ok_ratio": 0.5}], cost_usd=0.04)
    db.add(r)
    db.flush()
    return r


def test_hand_built_report_counts_and_flags(db):
    r = _bad_report(db)
    ev = evaluate_report(db, r)
    assert (ev.claims_total, ev.fact_claims, ev.citations_total, ev.invalid_citations) == (4, 4, 3, 1)
    assert ev.unsupported_claims == 3 and ev.unsupported_claim_rate == 0.75  # funding, uncited, nonexistent-only
    assert ev.citation_precision == 2 / 3 and ev.sources_used == 1 and ev.source_coverage == 0.5
    assert ev.latency_ms is None and "latency is null" in ev.notes and ev.cost_usd == 0.04
    high = {f["rule"] for f in ev.flags if f["severity"] == "HIGH"}
    assert high == {"funding_without_funding_source", "fact_without_citation", "nonexistent_citation"}
    guard = {f["rule"] for f in ev.flags if f["rule"].startswith("guard_")}
    assert guard == {"guard_removed_invalid_ids", "guard_downgraded_fact"}  # reported, not counted above

    first = promote_flags(db)
    assert first["created"] == 3
    rows = list(db.scalars(select(AgentFailure).order_by(AgentFailure.number)))
    assert [f.number for f in rows] == [1, 2, 3] and all(f.status == "OPEN" and f.origin == "evaluator" for f in rows)
    assert all(f.report_id == r.id and f.agent == "RADAR ANALYST" for f in rows)
    evaluate_report(db, r)
    assert promote_flags(db) == {"created": 0, "skipped": 3}
    assert db.scalar(select(func.count()).select_from(AgentFailure)) == 3


def test_human_review_survives_reevaluation(db):
    from radar.evals.failures import review_failure

    r = _bad_report(db)
    evaluate_report(db, r)
    promote_flags(db)
    review_failure(db, 1, status="CONFIRMED", note="real problem")
    ev = db.scalar(select(AgentRunEvaluation).where(AgentRunEvaluation.report_id == r.id))
    ev.notes = "reviewed by analyst"
    db.flush()
    again = evaluate_report(db, r)
    assert again.review_status == "HUMAN_REVIEWED" and again.notes == "reviewed by analyst"


def test_agent_metrics_null_with_reasons_when_empty(db):
    rows = {r["name"]: r for r in agent_metrics(db)}
    a = rows["RADAR ANALYST"]
    assert a["runs"] == 0 and a["metrics"]["success_rate"] == {"value": None, "n": 0,
                                                               "reason": "no evaluated runs (run `radar eval`)"}
    assert rows["NARRATIVES"]["metrics"]["latency_p50_ms"]["reason"].startswith("agent writes no report")
    assert rows["ANTI MEMO"]["metrics"]["fallback_rate"]["reason"] == "fallback is not recorded for this agent"
    assert [r["name"] for r in agent_metrics(db)][:3] == ["RADAR ANALYST", "MEETING BRIEF", "ANTI MEMO"]


@pytest.mark.usefixtures("seeded")
def test_generate_template_reports_is_idempotent_and_evaluable(monkeypatch):
    from radar.db import session_scope
    from radar.demo.warm import generate_template_reports
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "demo_synthetic", True)  # the seeded fixture is the labeled demo dataset

    with session_scope() as s:
        first = generate_template_reports(s, companies=3)
    assert first["briefs"] >= 1 and first["meeting_briefs"] >= 1
    with session_scope() as s:
        second = generate_template_reports(s, companies=3)
    assert second["briefs"] == 0 and second["meeting_briefs"] == 0 and second["skipped"] >= 2
    with session_scope() as s:
        evs = evaluate_all(s)
        rows = {r["name"]: r for r in agent_metrics(s)}
    assert len(evs) >= 2
    analyst = rows["RADAR ANALYST"]
    assert analyst["runs"] >= 1 and analyst["metrics"]["success_rate"]["value"] is not None
    assert analyst["metrics"]["latency_p50_ms"]["value"] is not None
    assert rows["MEETING BRIEF"]["runs"] >= 1

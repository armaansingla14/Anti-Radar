"""Portfolio Pulse engine end to end on a fictional scenario ingested through the real pipeline."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from radar.models import (
    Company,
    CompRelationship,
    EventImpact,
    MaterialEvent,
    PortfolioDecision,
    PortfolioPosition,
    PortfolioQuestion,
    PortfolioRun,
    ResearchReport,
)
from radar.portfolio.config import load_config
from radar.portfolio.run import run_portfolio
from radar.portfolio.sync import PrivateFieldError, assert_public_position, sync_portfolio
from tests import portfolio_scenario as sc


@pytest.fixture()
def world(db):
    sync = sc.build(db)
    out = run_portfolio(db, sc.DAY)
    return db, sync, out


def _event(db, title_part: str) -> MaterialEvent:
    return db.scalar(select(MaterialEvent).where(MaterialEvent.title.contains(title_part)))


def _company(db, slug: str) -> Company:
    return db.scalar(select(Company).where(Company.slug == slug))


def test_sync_matches_existing_companies_without_duplicates(world):
    db, sync, _ = world
    assert db.scalar(select(func.count()).select_from(Company).where(Company.name == "Orbitform")) == 1
    orbit = db.scalar(select(Company).where(Company.github_org == "orbitform"))
    assert orbit.domain == "orbitform.ai"  # matched by GitHub org, domain attached, not duplicated
    assert "acme-robotics" in sync["matched"] and "halcyon-fusion" in sync["created"]
    assert db.scalar(select(func.count()).select_from(PortfolioPosition)) == 2
    # Orbitform is a comp of both portfolio companies: one Company, two relationships.
    rels = list(db.scalars(select(CompRelationship).where(CompRelationship.comp_company_id == orbit.id)))
    assert sorted(r.comp_type for r in rels) == ["DIRECT", "INFRASTRUCTURE"]
    assert all(r.epistemic == "INFERENCE" and r.source_url for r in rels)
    again = sync_portfolio(db, load_config(sc.CONFIG))  # idempotent
    assert again["created"] == [] and again["comp_companies_created"] == []
    assert db.scalar(select(func.count()).select_from(Company)) == 4


def test_private_fields_stay_empty(world):
    db, _, _ = world
    for p in db.scalars(select(PortfolioPosition)):
        assert p.private_fields == {}
        p.private_fields = {"ownership": 0.05}
        with pytest.raises(PrivateFieldError):
            assert_public_position(p)
        p.private_fields = {}


def test_funnel_counts_are_real(world):
    db, _, out = world
    assert (out["raw_changes"], out["candidates"], out["after_dedup"]) == (15, 10, 9)
    assert (out["material"], out["watch"], out["noise"]) == (5, 1, 3)
    run = db.scalar(select(PortfolioRun).where(PortfolioRun.day == sc.DAY))
    assert run.counts["companies_surfaced"] == 2 and run.counts["per_company"]["acme-robotics"]["raw"] == 11
    assert "x" in run.providers and run.providers["x"]["state"] == "disabled"


def test_events_are_typed_classified_and_deduplicated(world):
    db, _, _ = world
    rnd = _event(db, "Series B")
    assert (rnd.event_type, rnd.event_subtype, rnd.materiality, rnd.materiality_rule) == (
        "FINANCING", "new_round", "MATERIAL", "M2")
    launch = _event(db, "Gelgrip 2")
    assert (launch.event_subtype, launch.materiality, launch.materiality_rule) == ("product_launch", "MATERIAL", "M4")
    assert launch.raw_count == 2 and len(launch.source_urls) == 2  # blog post + HN story merged, both kept
    assert launch.novelty and "90 days" in launch.novelty
    cluster = db.scalar(select(MaterialEvent).where(MaterialEvent.event_subtype == "specialized_hiring_cluster"))
    assert (cluster.materiality, cluster.materiality_rule, cluster.epistemic, cluster.raw_count) == (
        "WATCH", "M6", "INFERENCE", 6)
    assert cluster.suggested_review == "PREPARE_FOUNDER_QUESTIONS"
    award = _event(db, "magnet prototype")
    assert (award.event_subtype, award.materiality, award.materiality_rule) == (
        "government_contract", "MATERIAL", "M3")
    for t in ("offsite", "fusion summit"):
        assert _event(db, t).materiality == "NOISE" and _event(db, t).materiality_rule == "M10"
    copy = db.scalar(select(MaterialEvent).where(MaterialEvent.event_subtype == "minor_copy_update"))
    assert copy is not None and copy.materiality == "NOISE"
    assert all(e.why_it_matters for e in db.scalars(select(MaterialEvent)))


def test_competitor_impacts(world):
    db, _, _ = world
    acme, halcyon = _company(db, "acme-robotics"), _company(db, "halcyon-fusion")
    orbit = _event(db, "Orbit One")
    imps = {i.company_id: i for i in db.scalars(select(EventImpact).where(EventImpact.event_id == orbit.id))}
    assert imps[acme.id].relation == "COMPETITOR" and imps[acme.id].materiality_for_company == "MATERIAL"
    assert "dexterous robot hands" in imps[acme.id].impact_reason
    assert imps[halcyon.id].relation == "INFRASTRUCTURE" and imps[halcyon.id].materiality_for_company == "WATCH"
    grip = _event(db, "Gripworks raises")
    gi = db.scalar(select(EventImpact).where(EventImpact.event_id == grip.id))
    assert gi.relation == "ADJACENT" and gi.materiality_for_company == "WATCH"
    noise = _event(db, "offsite")
    assert db.scalar(select(func.count()).select_from(EventImpact).where(EventImpact.event_id == noise.id)) == 0


def test_needs_attention_ranking_and_actions(world):
    db, _, out = world
    assert out["needs_attention"] == ["acme-robotics", "halcyon-fusion"]
    from radar.portfolio import views

    cards = views.needs_attention(db, sc.DAY)
    acme, halcyon = cards
    assert acme["priority"] == "HIGH" and acme["suggested_action"] == "UPDATE_COMPETITIVE_MAP"
    assert halcyon["priority"] == "MEDIUM" and halcyon["suggested_action"] == "REVIEW"
    assert "material event" in acme["why_surfaced"] and acme["what_this_could_mean_epistemic"] == "INFERENCE"
    texts = " ".join(q["text"] for c in cards for q in c["questions"]).lower()
    assert "founder" not in texts or "contact" not in texts
    assert all(q["event_id"] and q["source_url"] for c in cards for q in c["questions"])


def test_questions_and_decision_log_are_idempotent(world):
    db, _, out = world
    nq = db.scalar(select(func.count()).select_from(PortfolioQuestion))
    nd = db.scalar(select(func.count()).select_from(PortfolioDecision))
    ids = sorted(str(e.id) for e in db.scalars(select(MaterialEvent)))
    assert nq >= 4 and out["questions_created"] == nq
    kinds = set(db.scalars(select(PortfolioDecision.kind)))
    assert {"detected", "classified", "question_raised"} <= kinds
    assert set(db.scalars(select(PortfolioDecision.actor))) == {"system"}  # no human step was invented
    again = run_portfolio(db, sc.DAY)
    assert again["questions_created"] == 0
    assert db.scalar(select(func.count()).select_from(PortfolioQuestion)) == nq
    assert db.scalar(select(func.count()).select_from(PortfolioDecision)) == nd
    assert sorted(str(e.id) for e in db.scalars(select(MaterialEvent))) == ids
    assert db.scalar(select(func.count()).select_from(PortfolioRun)) == 1


def test_reports_are_deterministic_and_pass_the_evaluator(world):
    db, _, _ = world
    from radar.evals.checks import check_report
    from radar.evals.metrics import evaluate_all, structured_valid
    from radar.portfolio.reports import build_brief, build_review

    brief = db.scalar(select(ResearchReport).where(ResearchReport.kind == "portfolio_brief"))
    reviews = list(db.scalars(select(ResearchReport).where(ResearchReport.kind == "portfolio_review")))
    assert brief is not None and len(reviews) == 2
    b1, m1, s1, _ = build_brief(db, sc.DAY)
    b2, m2, s2, _ = build_brief(db, sc.DAY)
    assert (b1, m1, s1) == (b2, m2, s2) and b1 == brief.body
    acme = _company(db, "acme-robotics")
    r1 = build_review(db, acme, sc.DAY)[:3]
    assert r1 == build_review(db, acme, sc.DAY)[:3]
    assert len(brief.body["five_things"]) == 5 and brief.body["noise_filtered"]["raw_changes"] == 15
    assert any(t["type"] == "timing" for t in brief.trace)
    for r in [brief, *reviews]:
        assert structured_valid(r.kind, r.body) == (True, None)
        res = check_report(db, r)
        assert res.invalid_citations == 0 and not res.unsupported, [f.as_dict() for f in res.flags]
        registered = {s["id"] for s in r.sources}
        assert res.cited_ids <= registered
    rows = evaluate_all(db, agent="PORTFOLIO ANALYST")
    assert len(rows) == 3 and all(x.agent == "PORTFOLIO ANALYST" and x.task_success for x in rows)
    assert all(x.latency_ms is not None and x.unsupported_claims == 0 for x in rows)


def test_metrics_windows(world):
    db, _, _ = world
    from radar.portfolio.metrics import compare_windows, stored_proxies

    acme = _company(db, "acme-robotics")
    p = stored_proxies(db, acme.id, sc.DAY)
    assert p["proxy_open_technical_roles"]["value"] == 9
    assert "not headcount" in p["proxy_open_technical_roles"]["proxy_label"]
    w = {x["metric"]: x for x in compare_windows(p)}
    assert w["new_technical_roles"]["comparable"] and w["new_technical_roles"]["current"] == 6
    assert w["releases"]["comparable"] is False and w["releases"]["reason"]
    halcyon = _company(db, "halcyon-fusion")
    hp = stored_proxies(db, halcyon.id, sc.DAY)
    assert hp["proxy_federal_award_amount_24m"]["value"] == 2_400_000
    assert "not revenue" in hp["proxy_federal_award_amount_24m"]["proxy_label"]

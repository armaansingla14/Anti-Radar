"""Portfolio API: pulse, company view, events, comps, brief, reviews; read-only snapshot; no private fields."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from radar.models import PortfolioDecision, PortfolioQuestion, ReviewDecision
from radar.portfolio.run import run_portfolio
from radar.settings import get_settings
from tests import portfolio_scenario as sc


@pytest.fixture()
def api(db, client, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_snapshot", False)
    sc.build(db)
    run_portfolio(db, sc.DAY)
    db.commit()
    return client


def _get(c, url):
    r = c.get(url)
    assert r.status_code == 200, (url, r.text)
    assert "private_fields" not in r.text and '"ownership"' not in r.text
    return r.json()


def test_pulse(api):
    p = _get(api, "/api/portfolio")
    assert p["counts"]["need_attention"] == 2 and p["counts"]["material"] >= 4 and p["counts"]["financing"] == 1
    assert p["counts"]["competitive"] >= 1 and p["counts"]["noise"] == 3
    f = p["funnel"]
    assert (f["raw_changes"], f["candidates"], f["after_dedup"], f["material"], f["watch"], f["noise"]) == (
        15, 10, 9, 5, 1, 3) and f["available"]
    assert [c["company"]["slug"] for c in p["needs_attention"]] == ["acme-robotics", "halcyon-fusion"]
    m = p["monitoring"]
    assert m["portfolio_companies"] == 2 and m["comps"] == 2 and m["tracked"] == 4
    assert any(x["key"] == "x" and x["state"] == "disabled" for x in m["providers"])
    assert any(x["metric"]["metric"] == "new_technical_roles" for x in p["movers"])
    assert all(x["metric"]["comparable"] for x in p["movers"])
    assert p["brief_id"]


def test_company_view_public_fields_only(api):
    v = _get(api, "/api/portfolio/acme-robotics")
    assert set(v["position"]) == {"portfolio_name", "status", "publicly_disclosed", "source_url",
                                  "source_checked_at", "investment_stage", "investment_year", "sector", "thesis_tags"}
    assert v["position"]["investment_stage"] == "Seed"
    assert {e["event_subtype"] for e in v["events"]} >= {"new_round", "product_launch", "specialized_hiring_cluster"}
    assert {e["relation"] for e in v["competitor_events"]} == {"COMPETITOR", "ADJACENT"}
    assert v["noise_count"] == 2 and v["report_id"] and v["diligence"]["status"] == "NOT_RUN"
    assert [c["comp_type"] for c in v["comps"]] == ["DIRECT", "ADJACENT"]
    assert any(q["status"] == "OPEN" for q in v["questions"])
    assert v["last_review"] is None and v["next_review"] is None
    assert v["decision_trace"] and v["data_age"]["families"]
    assert v["needs_attention"]["priority"] == "HIGH"
    h = _get(api, "/api/portfolio/halcyon-fusion")
    assert h["position"]["investment_stage"] is None  # unknown stays unknown
    assert api.get("/api/portfolio/orbitform").status_code == 404  # a comp is not a portfolio company


def test_events_filters_and_detail(api):
    allv = _get(api, "/api/portfolio/events")
    assert allv["total"] == 9
    mats = _get(api, "/api/portfolio/events?materiality=MATERIAL")
    assert mats["total"] == 5
    comp = _get(api, "/api/portfolio/events?company=acme-robotics&relation=COMPETITOR")
    assert comp["total"] == 1 and comp["items"][0]["relation"] == "COMPETITOR"
    fin = _get(api, "/api/portfolio/events?type=FINANCING&limit=1")
    assert fin["total"] == 2 and len(fin["items"]) == 1
    assert api.get("/api/portfolio/events?type=NOPE").status_code == 422
    eid = comp["items"][0]["id"]
    d = _get(api, f"/api/portfolio/events/{eid}")
    assert d["suggested_action"] in ("MONITOR", "UPDATE_COMPETITIVE_MAP")
    assert {i["relation"] for i in d["impacts"]} == {"COMPETITOR", "INFRASTRUCTURE"}
    assert d["event"]["sources"] and d["decision_trace"]
    assert api.get("/api/portfolio/events/not-a-uuid").status_code == 404


def test_comps_and_brief(api):
    maps = _get(api, "/api/portfolio/comps")
    assert len(maps) == 2
    m = _get(api, "/api/portfolio/comps/acme-robotics")
    assert [c["company"]["slug"] for c in m["direct"]] == ["orbitform"]
    assert m["direct"][0]["recent_events"] and m["direct"][0]["epistemic"] == "INFERENCE"
    assert [c["company"]["slug"] for c in m["adjacent"]] == ["gripworks"]
    b = _get(api, "/api/portfolio/brief")
    assert b["date"] == sc.DAY.isoformat() and b["body"]["five_things"] and b["sources"]
    arch = _get(api, "/api/portfolio/brief/archive")
    assert [a["date"] for a in arch] == [sc.DAY.isoformat()]
    assert _get(api, f"/api/portfolio/brief/day/{sc.DAY.isoformat()}")["id"] == b["id"]
    assert api.get("/api/portfolio/brief/day/2020-01-01").status_code == 404
    reports = _get(api, "/api/reports?kind=portfolio_review")
    assert len(reports) == 2


def test_review_and_question_writes(api, db):
    r = api.post("/api/portfolio/acme-robotics/review", json={"decision": "MARK_REVIEWED", "note": "seen",
                                                              "reviewer": "Ana"})
    assert r.status_code == 201 and r.json()["decision"] == "MARK_REVIEWED"
    assert api.post("/api/portfolio/acme-robotics/review", json={"decision": "ACCEPT"}).status_code == 422
    v = _get(api, "/api/portfolio/acme-robotics")
    assert v["last_review"]["reviewer"] == "Ana" and v["next_review"]
    assert any(d["kind"] == "reviewed" and d["actor"] == "Ana" for d in v["decision_trace"])
    q = v["questions"][0]
    r = api.post(f"/api/portfolio/questions/{q['id']}", json={"status": "ANSWERED", "reviewer": "Ana"})
    assert r.status_code == 200 and r.json()["status"] == "ANSWERED"
    assert api.post("/api/portfolio/questions/00000000-0000-0000-0000-000000000000",
                    json={"status": "ANSWERED"}).status_code == 404
    db.expire_all()
    assert db.scalar(select(ReviewDecision).where(ReviewDecision.target_type == "portfolio_company"))
    assert db.get(PortfolioQuestion, __import__("uuid").UUID(q["id"])).status == "ANSWERED"
    assert {d.actor for d in db.scalars(select(PortfolioDecision).where(PortfolioDecision.kind == "reviewed"))} == {
        "Ana"}


def test_writes_refused_on_snapshot(api, monkeypatch):
    monkeypatch.setattr(get_settings(), "demo_snapshot", True)
    r = api.post("/api/portfolio/acme-robotics/review", json={"decision": "MARK_REVIEWED"})
    assert r.status_code == 403 and r.json()["detail"] == "snapshot_read_only"
    r = api.post("/api/portfolio/questions/00000000-0000-0000-0000-000000000000", json={"status": "ANSWERED"})
    assert r.status_code == 403 and r.json()["detail"] == "snapshot_read_only"


def test_no_private_fields_in_any_response(api, db):
    from radar.models import PortfolioPosition

    # Even if a row carried private data (firm mode), no endpoint serialises it.
    for p in db.scalars(select(PortfolioPosition)):
        p.private_fields = {"ownership": 0.07, "cost_basis": 1_000_000}
    db.commit()
    urls = ["/api/portfolio", "/api/portfolio/companies", "/api/portfolio/acme-robotics", "/api/portfolio/comps",
            "/api/portfolio/comps/acme-robotics", "/api/portfolio/events", "/api/portfolio/brief",
            "/api/portfolio/brief/archive"]
    for u in urls:
        body = _get(api, u)
        assert "0.07" not in json.dumps(body) and "cost_basis" not in json.dumps(body)
    for p in db.scalars(select(PortfolioPosition)):
        p.private_fields = {}
    db.commit()


def test_monitoring_freshness_and_staleness(api, db):
    from datetime import timedelta

    from radar.models import Company, Signal

    grip = db.scalar(select(Company).where(Company.slug == "gripworks"))
    for s in db.scalars(select(Signal).where(Signal.entity_id == grip.id)):
        s.detected_at = s.detected_at - timedelta(days=20)
    db.commit()
    m = _get(api, "/api/portfolio")["monitoring"]
    assert m["tracked"] == m["fresh"] + m["stale"] + m["never_observed"] == 4
    assert m["stale"] == 1 and m["stale_after_days"] == 7 and m["events_updated_at"]

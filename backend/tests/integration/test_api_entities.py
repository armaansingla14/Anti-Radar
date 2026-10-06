"""Integration tests: companies, founders, signals and reports endpoints on the seeded fictional dataset."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from radar import clock
from radar.db import session_scope
from radar.models import Company, ResearchReport

DEMO = {"include_demo": "true"}


def _names(rows: list[dict]) -> list[str]:
    return [r["name"] for r in rows]


# ── company detail ───────────────────────────────────────────────────────────


def test_company_detail_halcyon(seeded, client):
    r = client.get("/api/companies/halcyon-dynamics")
    assert r.status_code == 200
    d = r.json()
    assert d["summary"]["name"] == "Halcyon Dynamics" and d["summary"]["is_demo"] is True
    assert "DEMO" in d["data_note"]

    facts = {f["label"]: f for f in d["facts"]}
    assert {"Founded", "Website", "Location", "Stage estimate", "GitHub org", "X handle", "Team size known",
            "Open roles"} <= set(facts)
    assert {f["status"] for f in d["facts"]} >= {"VERIFIED", "INFERRED"}
    assert facts["Website"]["status"] == "VERIFIED" and facts["Website"]["source_url"]
    assert facts["Stage estimate"]["status"] == "INFERRED" and facts["Stage estimate"]["note"]
    assert facts["Open roles"]["status"] == "VERIFIED" and facts["Open roles"]["value"] == 3

    # timeline newest first, bounded
    days = [t["day"] for t in d["timeline"]]
    assert days and days == sorted(days, reverse=True) and len(days) <= 40
    assert {"job_posted", "star_velocity"} <= {t["signal_type"] for t in d["timeline"]}

    # series: stars non-empty, labeled with its provenance
    series = {s["metric"]: s for s in d["series"]}
    assert series["stars"]["points"] and series["stars"]["source"] == "demo" and series["stars"]["note"]
    vals = [p["value"] for p in series["stars"]["points"]]
    assert vals == sorted(vals) and vals[-1] > 0  # cumulative
    assert "commits_week" in series

    founders = d["founders"]
    assert founders[0]["is_founder"] and founders[1]["is_founder"]
    assert {f["name"] for f in founders[:2]} == {"Mira Okafor", "Dev Raman"}
    assert not founders[-1]["is_founder"] and len(founders) == 5

    repos = {r["full_name"]: r for r in d["repositories"]}
    assert repos["demo-halcyon/tactile-vla"]["stars_30d"] > 0 and repos["demo-halcyon/tactile-vla"]["commits_4w"]
    assert any("TactileVLA" in p["title"] for p in d["papers"])
    assert len(d["jobs"]) == 3 and d["hiring"]["open_roles"] == 3
    assert d["sources"] and all(s["url"] for s in d["sources"])

    assert d["anti_score"]["total"] == d["summary"]["score"] and d["anti_score"]["components"]
    hist = [p["day"] for p in d["score_history"]]
    assert len(hist) >= 5 and hist == sorted(hist)
    assert d["why_now"]["generator"] == "template" and d["why_now"]["citations"]
    assert d["why_anti"]["text"] and len(d["risks"]) >= 5
    assert d["reports"] == [] and d["match_candidates"] == [] and d["watched"] is False


def test_company_detail_by_id_score_and_404(seeded, client):
    with session_scope() as s:
        cid = str(s.scalar(select(Company.id).where(Company.slug == "tessellate-compute")))
    assert client.get(f"/api/companies/{cid}").json()["summary"]["slug"] == "tessellate-compute"
    sc = client.get("/api/companies/halcyon-dynamics/score")
    assert sc.status_code == 200
    body = sc.json()
    assert body["total"] >= 60 and {c["key"] for c in body["components"]} >= {"technical_momentum", "thesis_fit"}
    assert body["delta_7d"] is not None
    assert client.get("/api/companies/no-such-company").status_code == 404
    assert client.get("/api/companies/no-such-company/score").status_code == 404
    assert client.get("/api/companies/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/companies/bad slug!").status_code == 422


def test_project_company_has_unknown_facts(seeded, client):
    d = client.get("/api/companies/demo-sq-propulsion").json()
    facts = {f["label"]: f for f in d["facts"]}
    assert facts["Website"]["status"] == "UNKNOWN" and facts["Website"]["value"] is None
    assert facts["Open roles"]["status"] == "UNKNOWN"
    assert {f["role"] for f in d["founders"]} == {"Member"}


# ── companies list ───────────────────────────────────────────────────────────


def test_companies_list_filters_and_sorts(seeded, client):
    rows = client.get("/api/companies", params=DEMO).json()
    assert isinstance(rows, list) and rows[0]["name"] == "Halcyon Dynamics" and len(rows) == 7
    scores = [r["score"] for r in rows]
    assert scores == sorted(scores, reverse=True)

    delta = client.get("/api/companies", params={**DEMO, "sort": "delta"}).json()
    assert delta[-1]["name"] == "Tessellate Compute"  # fell 8 points this week
    stars = client.get("/api/companies", params={**DEMO, "sort": "stars"}).json()
    assert stars[0]["name"] == "Tessellate Compute"
    recent = client.get("/api/companies", params={**DEMO, "sort": "recent"}).json()
    assert recent[0]["slug"] == "demo-sq-propulsion"
    gap = client.get("/api/companies", params={**DEMO, "sort": "attention_gap"}).json()
    gaps = [r["attention_gap"] for r in gap if r["attention_gap"] is not None]
    assert gaps == sorted(gaps, reverse=True)

    assert _names(client.get("/api/companies", params={**DEMO, "q": "FUSION"}).json()) == ["Perihelion Fusion"]
    assert set(_names(client.get("/api/companies", params={**DEMO, "theme": "defense"}).json())) >= {
        "Kestrel Autonomy"}
    assert set(_names(client.get("/api/companies", params={**DEMO, "stage": "seed"}).json())) == {
        "Tessellate Compute", "Orbitform"}
    assert _names(client.get("/api/companies", params={**DEMO, "kind": "project"}).json()) == ["demo-sq-propulsion"]
    assert client.get("/api/companies", params={"include_demo": "false"}).json() == []

    page = client.get("/api/companies", params={**DEMO, "limit": 2, "offset": 1}).json()
    assert _names(page) == _names(rows[1:3])

    assert client.get("/api/companies", params={"sort": "vibes"}).status_code == 422
    assert client.get("/api/companies", params={"limit": 500}).status_code == 422
    assert client.get("/api/companies", params={"theme": "nope"}).status_code == 422


# ── founders ─────────────────────────────────────────────────────────────────


def test_founder_detail_sana_qureshi(seeded, client):
    r = client.get("/api/founders/sana-qureshi")
    assert r.status_code == 200
    d = r.json()
    card = d["card"]
    assert card["name"] == "Sana Qureshi" and card["current_company"] is None
    assert "Coriolis Aerospace" in card["prior_affiliations"] and card["is_demo"]
    facts = {f["label"]: f for f in d["facts"]}
    assert facts["Current company"]["status"] == "UNKNOWN" and facts["Current company"]["value"] is None
    assert "Coriolis Aerospace" in facts["Prior affiliations"]["value"]
    assert facts["GitHub"]["status"] == "VERIFIED" and facts["GitHub"]["source_url"]
    assert {f["status"] for f in d["facts"]} == {"VERIFIED", "INFERRED", "UNKNOWN"}
    assert _names(d["companies"]) == ["demo-sq-propulsion"]
    assert [r["full_name"] for r in d["repositories"]] == ["demo-sq-propulsion/rde-sim"]
    assert set(_names(d["collaborators"])) == {"Ilse Varga", "Marcus Bell"}
    assert d["founder_score"]["total"] == card["founder_score"]
    assert d["timeline"] and d["why_now"]["text"] and "DEMO" in d["data_note"]

    assert client.get("/api/founders/sana-qureshi/score").json()["total"] == card["founder_score"]
    assert client.get("/api/founders/nobody-here").status_code == 404
    assert client.get("/api/founders/nobody-here/score").status_code == 404


def test_founder_detail_elias_papers(seeded, client):
    d = client.get("/api/founders/elias-brandt").json()
    assert len(d["papers"]) == 3 and all(p["code_urls"] for p in d["papers"])
    assert [r["full_name"] for r in d["repositories"]] == ["demo-ebrandt/dreamgrid"]


def test_founders_list(seeded, client):
    rows = client.get("/api/founders", params=DEMO).json()
    assert rows[0]["name"] == "Elias Brandt"
    scores = [r["founder_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)
    no_co = client.get("/api/founders", params={**DEMO, "no_company": "true"}).json()
    assert set(_names(no_co)) == {"Sana Qureshi", "Marcus Bell", "Ilse Varga"}
    assert all(p["current_company"] is None for p in no_co)
    assert _names(client.get("/api/founders", params={**DEMO, "q": "qureshi"}).json()) == ["Sana Qureshi"]
    robotics = _names(client.get("/api/founders", params={**DEMO, "theme": "robotics"}).json())
    assert "Elias Brandt" in robotics and "Sana Qureshi" not in robotics
    assert client.get("/api/founders", params={"sort": "nope"}).status_code == 422
    assert client.get("/api/founders", params={"include_demo": "false"}).json() == []


# ── signals ──────────────────────────────────────────────────────────────────


def test_signals_filters(seeded, client):
    with session_scope() as s:
        cid = str(s.scalar(select(Company.id).where(Company.slug == "halcyon-dynamics")))
    rows = client.get("/api/signals", params={**DEMO, "entity_id": cid}).json()
    assert rows and any(r["entity_name"] == "Halcyon Dynamics" and r["entity_slug"] == "halcyon-dynamics"
                        for r in rows)
    assert all(r["entity_name"] for r in rows)
    times = [r["event_at"] for r in rows]
    assert times == sorted(times, reverse=True)

    jobs = client.get("/api/signals", params={**DEMO, "signal_type": "job_posted"}).json()
    assert jobs and all(r["signal_type"] == "job_posted" and r["is_demo"] for r in jobs)
    arxiv = client.get("/api/signals", params={**DEMO, "source_type": "arxiv", "entity_type": "paper"}).json()
    assert arxiv and all(r["source_type"] == "arxiv" and r["entity_type"] == "paper" for r in arxiv)
    since = "2026-10-04T00:00:00Z"
    recent = client.get("/api/signals", params={**DEMO, "since": since}).json()
    assert recent and all(datetime.fromisoformat(r["event_at"]) >=
                          datetime.fromisoformat("2026-10-04T00:00:00+00:00") for r in recent)
    assert len(client.get("/api/signals", params={**DEMO, "limit": 2}).json()) == 2
    assert client.get("/api/signals", params={"include_demo": "false"}).json() == []
    assert client.get("/api/signals", params={"entity_id": "nope"}).status_code == 422
    assert client.get("/api/signals", params={"limit": 501}).status_code == 422
    assert client.get("/api/signals", params={"entity_type": "galaxy"}).status_code == 422


# ── reports ──────────────────────────────────────────────────────────────────


def test_reports_json_markdown_and_list(seeded, client):
    with session_scope() as s:
        cid = s.scalar(select(Company.id).where(Company.slug == "halcyon-dynamics"))
        rep = ResearchReport(kind="memo", entity_type="company", entity_id=cid, title="Anti Memo — Halcyon Dynamics",
                             body={"sections": []}, markdown="# Anti Memo\n\nBody [S1]\n",
                             sources=[{"id": "S1", "url": "https://example.com"}], generator="template",
                             status="complete", is_demo=True)
        s.add(rep)
        s.flush()
        rid = str(rep.id)

    r = client.get(f"/api/reports/{rid}")
    assert r.status_code == 200
    body = r.json()
    assert body["entity_slug"] == "halcyon-dynamics" and body["markdown"].startswith("# Anti Memo")
    assert body["sources"][0]["id"] == "S1"

    md = client.get(f"/api/reports/{rid}/markdown")
    assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
    cd = md.headers["content-disposition"]
    assert cd.startswith("attachment") and "anti-memo-halcyon-dynamics-" in cd and cd.endswith('.md"')
    assert md.text.startswith("# Anti Memo")

    listing = client.get("/api/reports", params={"entity_id": str(cid)}).json()
    assert [x["id"] for x in listing] == [rid] and listing[0]["markdown"] == ""  # light
    assert client.get("/api/reports", params={"kind": "brief"}).json() == []

    detail = client.get("/api/companies/halcyon-dynamics").json()
    assert detail["reports"][0]["id"] == rid

    assert client.get("/api/reports/not-a-uuid").status_code == 404
    assert client.get("/api/reports/00000000-0000-0000-0000-000000000000/markdown").status_code == 404
    assert client.get("/api/reports", params={"entity_id": "x"}).status_code == 422


def test_entity_endpoints_are_fast(seeded, client):
    import time

    client.get("/api/companies/halcyon-dynamics")  # warm
    for path in ("/api/companies", "/api/companies/halcyon-dynamics", "/api/founders",
                 "/api/founders/sana-qureshi", "/api/signals"):
        t = time.perf_counter()
        assert client.get(path).status_code == 200
        assert time.perf_counter() - t < 1.0, path
    assert clock.today().isoformat() == "2026-10-06"

"""Portfolio → diligence handoff: a run started from a MaterialEvent collects the event's own pages, records the
event in its trace and logs a ``diligence_requested`` decision (live runs only)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from radar.api import ratelimit
from radar.diligence.capture import load
from radar.diligence.collect import CollectResult
from radar.diligence.run import DiligenceError, run_diligence
from radar.models import Company, MaterialEvent, PortfolioDecision
from radar.settings import get_settings
from tests.fixtures.diligence.helpers import SAMPLE_CAPTURE

POST = "https://modal.example/blog/nvidia-vera-sandboxes"


def _company(db, slug: str = "modal-fixture") -> Company:
    c = Company(slug=slug, name=slug.title(), website=f"https://{slug}.example", domain=f"{slug}.example",
                thesis_tags=[])
    db.add(c)
    db.flush()
    return c


def _event(db, company: Company, urls: list[str]) -> MaterialEvent:
    now = datetime(2026, 11, 6, 12, tzinfo=UTC)
    ev = MaterialEvent(company_id=company.id, event_type="TECHNICAL", event_subtype="performance_claim",
                       title="NVIDIA Vera CPUs double throughput for Modal Sandboxes", event_at=now, detected_at=now,
                       source_urls=urls, materiality="MATERIAL", materiality_rule="M3", suggested_review="RUN_DILIGENCE",
                       dedup_key=uuid.uuid4().hex)
    db.add(ev)
    db.flush()
    return ev


@pytest.fixture()
def fake_collect(monkeypatch):
    cap = load(SAMPLE_CAPTURE)
    seen: dict = {}

    def fake(session, company, *, budget_s, fetcher=None, seed_urls=None):
        seen["seed_urls"] = seed_urls
        return CollectResult(artifacts=cap.artifacts, trace=list(cap.trace), facts=cap.facts,
                             collected_at=cap.collected_at)

    monkeypatch.setattr("radar.diligence.run.collect", fake)
    return seen


def test_run_from_event_seeds_its_urls_traces_the_handoff_and_logs_the_decision(db, fake_collect):
    c = _company(db)
    ev = _event(db, c, [POST])
    run = run_diligence(db, c, live=True, event_id=str(ev.id), seed_urls=["https://modal-fixture.example/pricing"],
                        requested_by="Jane Analyst")
    assert run.status == "complete", run.error
    assert fake_collect["seed_urls"] == ["https://modal-fixture.example/pricing", POST]
    handoff = [t for t in run.trace if t.get("type") == "handoff"]
    assert handoff == [{"type": "handoff", "event_id": str(ev.id),
                        "seed_urls": ["https://modal-fixture.example/pricing", POST]}]
    d = db.scalars(select(PortfolioDecision).where(PortfolioDecision.company_id == c.id)).all()
    assert [(x.kind, x.actor, x.event_id) for x in d] == [("diligence_requested", "Jane Analyst", ev.id)]
    assert "NVIDIA Vera" in d[0].note


def test_plain_runs_and_replays_log_nothing_and_events_must_belong_to_the_company(db, fake_collect):
    c, other = _company(db), _company(db, "other-co")
    ev = _event(db, other, [POST])
    with pytest.raises(DiligenceError, match="not about"):
        run_diligence(db, c, live=True, event_id=ev.id)
    with pytest.raises(DiligenceError, match="no material event"):
        run_diligence(db, c, live=True, event_id=uuid.uuid4())
    with pytest.raises(DiligenceError, match="live runs"):
        run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE, seed_urls=[POST])
    run = run_diligence(db, c, live=True)  # existing behaviour: no seeds, no handoff entry
    assert run.status == "complete" and fake_collect["seed_urls"] is None
    assert not [t for t in run.trace if t.get("type") == "handoff"]
    replay = run_diligence(db, c, live=False, capture_path=SAMPLE_CAPTURE)
    assert replay.status == "complete"
    assert db.scalars(select(PortfolioDecision).where(PortfolioDecision.company_id == c.id)).all() == []


def test_run_endpoint_accepts_an_event_of_that_company(client, db, fake_collect, monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_token", None)
    monkeypatch.setattr(get_settings(), "demo_snapshot", False)
    c, other = _company(db), _company(db, "other-co")
    ev, foreign = _event(db, c, [POST]), _event(db, other, ["https://other-co.example/x"])
    db.commit()
    assert client.post(f"/api/diligence/{c.slug}/run", json={"event_id": str(foreign.id)}).json() == \
        {"detail": "event_not_for_company"}
    assert client.post(f"/api/diligence/{c.slug}/run", json={"event_id": str(uuid.uuid4())}).status_code == 404
    assert client.post(f"/api/diligence/{c.slug}/run", json={"event_id": "nope"}).status_code == 422
    ratelimit.reset()  # three runs a minute; the refusals above used the budget
    r = client.post(f"/api/diligence/{c.slug}/run", json={"budget_s": 20, "event_id": str(ev.id)})
    assert r.status_code == 200 and r.json()["status"] in ("NEEDS_REVIEW", "COMPLETE")
    assert fake_collect["seed_urls"] == [POST]
    db.expire_all()
    d = db.scalars(select(PortfolioDecision).where(PortfolioDecision.company_id == c.id)).all()
    assert [(x.kind, x.event_id) for x in d] == [("diligence_requested", ev.id)]  # logged once, against the event

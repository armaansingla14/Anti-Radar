"""Seed replay: a captured link with portfolio.yaml syncs positions before its batches, and the post-seed portfolio
step runs the monitoring pass (its reports are evaluated with everything else)."""

from __future__ import annotations

import shutil
from datetime import date

import pytest
from sqlalchemy import func, select

from radar import clock
from radar.db import session_scope
from radar.models import Company, MaterialEvent, PortfolioPosition, ResearchReport
from radar.providers.base import CompanyRecord, ProviderBatch
from tests import portfolio_scenario as sc
from tests.integration.test_seed_chain import write_link

DAY = date(2026, 10, 6)


@pytest.fixture()
def link(tmp_path, db_url, monkeypatch):
    from radar.demo.seed import reset_database
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "today_override", None)
    monkeypatch.setattr(get_settings(), "demo_synthetic", False)
    sigs, srcs = zip(*[
        sc.post(sc.ACME, "https://acme-robotics.com/news/series-b", "Acme Robotics raises $40M Series B", sc.at(10, 1)),
        sc.post(sc.ORBIT, "https://orbitform.ai/blog/orbit-one", "Orbitform launches Orbit One humanoid hand",
                sc.at(10, 3)),
    ], strict=True)
    batch = ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=sc.ACME, name="ACME | tactile grippers", website="https://acme-robotics.com")],
        signals=list(sigs), sources=list(srcs))
    d = write_link(tmp_path / "snapshot", DAY, [batch])
    shutil.copyfile(sc.CONFIG, d / "portfolio.yaml")
    reset_database()
    yield [d]
    reset_database()
    clock.set_as_of(None)


def test_seed_syncs_captured_portfolio_and_runs_monitoring(link):
    from radar.demo.seed import seed_demo

    out = seed_demo(chain=link, fictional=False, llm_outputs=False, history=False)
    assert out["links"][0]["portfolio_sync"]["companies"] == 2
    run = out["portfolio"]["run"]
    assert run["day"] == DAY.isoformat() and run["material"] == 2 and run["companies_surfaced"] == 1
    assert out["evaluations"]["reports_evaluated"] >= 2
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(PortfolioPosition)) == 2
        acme = s.scalar(select(Company).where(Company.domain == "acme-robotics.com"))
        assert acme.name == "Acme Robotics"  # synced before the batch: the configured name, no duplicate
        assert s.scalar(select(func.count()).select_from(Company).where(Company.domain == "acme-robotics.com")) == 1
        assert s.scalar(select(func.count()).select_from(MaterialEvent)) == 2
        assert s.scalar(select(func.count()).select_from(ResearchReport).where(
            ResearchReport.kind == "portfolio_brief")) == 1
    again = seed_demo(chain=link, fictional=False, llm_outputs=False, history=False)  # idempotent re-run
    assert again["links"][0].get("skipped") == "already replayed"
    assert again["portfolio"]["run"]["material"] == 2


def test_seed_without_captured_portfolio_skips(db_url):
    from radar.demo.seed import reset_database, seed_portfolio

    reset_database()
    assert "skipped" in seed_portfolio([])

"""Hand audit of the first live portfolio monitoring run (Nov 6, 2026): identity and proxy regressions."""

from __future__ import annotations

import copy

import yaml
from sqlalchemy import func, select

from radar.models import Company, MaterialEvent, Signal
from radar.pipeline.ingest import ingest_batch
from radar.portfolio.config import parse_config
from radar.portfolio.metrics import stored_proxies
from radar.portfolio.run import run_portfolio
from radar.portfolio.sync import sync_portfolio
from radar.providers.base import CompanyRecord, ProviderBatch, domain_ref
from tests import portfolio_scenario as sc


def _cfg(**company):
    data = yaml.safe_load(sc.CONFIG.read_text())
    data["companies"] = [copy.deepcopy(data["companies"][0]) | company]
    data["companies"][0]["comps"] = []
    return parse_config(data)


def test_configured_slug_is_used_when_creating(db):
    """#11: an unrelated 'enigma' exists; the configured company is created as 'enigma-inc', not 'enigma-2'."""
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("enigma.design"), name="Enigma", website="https://enigma.design")]))
    out = sync_portfolio(db, _cfg(name="Enigma", slug="enigma-inc", website="https://enigma.inc", github_org=None,
                                  ats=None, newsroom_url=None))
    assert out["created"] == ["enigma-inc"]
    assert db.scalar(select(Company.domain).where(Company.slug == "enigma-inc")) == "enigma.inc"
    assert db.scalar(select(Company.domain).where(Company.slug == "enigma")) == "enigma.design"
    # Re-sync (or a company already known by domain) keeps the existing row and never renames it.
    again = sync_portfolio(db, _cfg(name="Enigma", slug="enigma-inc", website="https://enigma.inc", github_org=None,
                                    ats=None, newsroom_url=None))
    assert again["created"] == [] and db.scalar(select(func.count()).select_from(Company)) == 2


def test_redirect_domain_from_config_never_creates_a_second_company(db):
    """#10 (identity): batches captured on the domain the website redirects to (cognition.ai -> cognition.com) land
    on the configured company, through the newsroom / alias domains the config verifies."""
    sync_portfolio(db, _cfg(name="Cognition", website="https://cognition.ai", github_org=None, ats=None,
                            newsroom_url="https://cognition.com/blog", alias_domains=["cognition.com"]))
    sig, src = sc.post(domain_ref("cognition.com"), "https://cognition.com/blog/swe-2-flash", "Introducing SWE-2 Flash",
                       sc.at(10, 4))
    ingest_batch(db, ProviderBatch(provider="website", companies=[
        CompanyRecord(ref=domain_ref("cognition.com"), name="Cognition", website="https://cognition.com",
                      alt_refs=[domain_ref("cognition.ai")])], signals=[sig], sources=[src]))
    rows = list(db.scalars(select(Company).where(Company.name == "Cognition")))
    assert len(rows) == 1 and rows[0].domain == "cognition.ai"
    assert db.scalar(select(Signal.entity_id).where(Signal.signal_type == "company_post")) == rows[0].id


def test_customer_stories_are_noise_and_counted_in_a_proxy(db):
    """#3"""
    sc.build(db)
    sig, src = sc.post(sc.ACME, "https://acme-robotics.com/customers/example-foods",
                       "How Example Foods automated packing with Acme Robotics", sc.at(10, 4))
    ingest_batch(db, ProviderBatch(provider="website", signals=[sig], sources=[src]))
    run_portfolio(db, sc.DAY)
    ev = db.scalar(select(MaterialEvent).where(MaterialEvent.title.contains("Example Foods")))
    assert (ev.event_type, ev.event_subtype, ev.materiality) == ("COMMERCIAL", "customer_story", "NOISE")
    acme = db.scalar(select(Company).where(Company.slug == "acme-robotics"))
    p = stored_proxies(db, acme.id, sc.DAY)["proxy_customer_stories_30d"]
    assert p["value"] == 1 and "not revenue" in p["proxy_label"]

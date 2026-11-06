"""Sync the public portfolio config onto Company entities: positions and comps.

Every portfolio company and comp is matched to an existing ``Company`` through the normal entity-resolution keys
(``pipeline.resolve.Resolver``: domain, then GitHub org; an explicit ``slug`` in the config wins). A Company is
created only when nothing matches, exactly as a provider would create it (kind 'company', website, domain and
GitHub org set, first seen now, not demo). Re-running is idempotent.

Public mode: ``PortfolioPosition.private_fields`` must stay ``{}``. ``assert_public_position`` raises before a
non-empty value could be written, and nothing in the API serialises the column.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import Company, CompRelationship, PortfolioPosition
from radar.pipeline.resolve import Resolver
from radar.portfolio.config import CompanyConfig, CompConfig, PortfolioConfig, config_domain, own_domains
from radar.portfolio.decisions import log_system
from radar.providers.base import CompanyRecord, EntityRef, domain_ref, gh_org_ref

PROVIDER = "portfolio"


class PrivateFieldError(ValueError):
    """A public-mode write tried to populate PortfolioPosition.private_fields."""


def assert_public_position(position: PortfolioPosition) -> None:
    if position.private_fields:
        raise PrivateFieldError("private_fields must stay empty in public mode")


@dataclass
class Matched:
    company: Company
    created: bool
    via: str


def _record(cfg: CompanyConfig | CompConfig, source_url: str) -> tuple[EntityRef, CompanyRecord]:
    dom = config_domain(cfg.website)
    ref = domain_ref(dom) if dom else gh_org_ref(cfg.github_org or "")
    alts = [gh_org_ref(cfg.github_org)] if cfg.github_org and dom else []
    rec = CompanyRecord(ref=ref, name=cfg.name, website=cfg.website, github_org=cfg.github_org, kind="company",
                        alt_refs=alts, source_url=source_url)
    return ref, rec


def match_company(session: Session, resolver: Resolver, cfg: CompanyConfig | CompConfig, source_url: str) -> Matched:
    """Existing Company by config slug, domain or GitHub org; created only when nothing matches."""
    slug = getattr(cfg, "slug", None)
    if slug:
        c = session.scalar(select(Company).where(Company.slug == slug))
        if c is not None:
            dom = config_domain(cfg.website)
            if not c.domain and dom and resolver.lookup(domain_ref(dom)) is None:
                c.domain = dom
            if not c.github_org and cfg.github_org and resolver.lookup(gh_org_ref(cfg.github_org)) is None:
                c.github_org = cfg.github_org
            if not c.website:
                c.website = cfg.website
            doms = own_domains(cfg)
            if (c.attrs or {}).get("portfolio_domains") != doms:
                c.attrs = {**(c.attrs or {}), "portfolio_domains": doms}
            return Matched(c, False, "slug")
    ref, rec = _record(cfg, source_url)
    res = resolver.resolve(ref, rec)
    if res is None or not isinstance(res.entity, Company):
        raise LookupError(f"cannot resolve company {cfg.name}")
    c = res.entity
    if res.created:
        c.first_seen_at = clock.now()
        c.is_demo = False
        c.kind = "company"
        # A configured slug names a company that does not exist yet: create it under that slug when free
        # (e.g. 'enigma-inc' while an unrelated 'enigma' exists), instead of an auto-numbered one.
        if slug and c.slug != slug and session.scalar(select(Company.id).where(Company.slug == slug)) is None:
            c.slug = slug
    if not c.website:
        c.website = cfg.website
    doms = own_domains(cfg)
    if (c.attrs or {}).get("portfolio_domains") != doms:
        c.attrs = {**(c.attrs or {}), "portfolio_domains": doms}
    return Matched(c, res.created, res.via)


def sync_portfolio(session: Session, cfg: PortfolioConfig) -> dict[str, Any]:
    """Upsert positions and comps for ``cfg``. Positions and comps no longer in the config are removed."""
    resolver = Resolver(session, provider=PROVIDER)
    src = cfg.source.url
    out: dict[str, Any] = {"companies": 0, "created": [], "matched": [], "comps": 0, "comp_companies_created": [],
                           "removed_positions": 0}
    keep_positions: set[uuid.UUID] = set()
    for cc in cfg.companies:
        m = match_company(session, resolver, cc, src)
        company = m.company
        (out["created"] if m.created else out["matched"]).append(company.slug)
        pos = session.scalar(select(PortfolioPosition).where(PortfolioPosition.company_id == company.id,
                                                             PortfolioPosition.portfolio_name == cfg.portfolio_name))
        if pos is None:
            pos = PortfolioPosition(id=uuid.uuid4(), company_id=company.id, portfolio_name=cfg.portfolio_name,
                                    private_fields={})
            session.add(pos)
        pos.status = cc.status
        pos.publicly_disclosed = True
        pos.source_url = src
        pos.source_checked_at = cfg.source.checked_at
        pos.investment_stage = cc.public_stage
        pos.investment_year = cc.public_year
        pos.sector = cc.sector
        pos.thesis_tags = list(cc.thesis_tags)
        pos.is_demo = False
        pos.meta = {
            "config_name": cc.name, "website": cc.website, "github_org": cc.github_org,
            "ats": cc.ats.model_dump() if cc.ats else None, "newsroom_url": cc.newsroom_url,
            "usaspending_recipient": cc.usaspending_recipient, "note": cc.note,
            "source_note": cfg.source.note,
        }
        assert_public_position(pos)
        keep_positions.add(company.id)
        out["companies"] += 1

        existing = {r.comp_company_id: r for r in session.scalars(
            select(CompRelationship).where(CompRelationship.company_id == company.id))}
        kept: set[uuid.UUID] = set()
        added = 0
        for comp in cc.comps:
            cm = match_company(session, resolver, comp, comp.source_url)
            if cm.created:
                out["comp_companies_created"].append(cm.company.slug)
            if cm.company.id == company.id:
                continue  # a company is never its own comp
            row = existing.get(cm.company.id)
            if row is None:
                row = CompRelationship(id=uuid.uuid4(), company_id=company.id, comp_company_id=cm.company.id)
                session.add(row)
                existing[cm.company.id] = row
                added += 1
            row.comp_type = comp.type
            row.why_comparable = comp.why_comparable
            row.source_url = comp.source_url
            row.confidence = comp.confidence
            row.epistemic = "INFERENCE"
            row.is_demo = False
            kept.add(cm.company.id)
            out["comps"] += 1
        removed = [cid for cid in existing if cid not in kept]
        if removed:
            session.execute(delete(CompRelationship).where(CompRelationship.company_id == company.id,
                                                           CompRelationship.comp_company_id.in_(removed)))
        if added or removed:
            log_system(session, company_id=company.id, kind="comps_updated",
                       note=f"comps synced from the public portfolio config: +{added}, -{len(removed)}",
                       key_parts=(company.id, sorted(map(str, kept))))
    gone = [p for p in session.scalars(select(PortfolioPosition).where(
        PortfolioPosition.portfolio_name == cfg.portfolio_name)) if p.company_id not in keep_positions]
    for p in gone:
        session.delete(p)
    out["removed_positions"] = len(gone)
    session.flush()
    return out


@dataclass(frozen=True)
class CaptureTarget:
    """One company to collect for (a portfolio company or a comp), with its verified identifiers."""

    company_id: uuid.UUID
    name: str
    website: str
    domain: str | None
    github_org: str | None
    ats: tuple[str, str] | None
    newsroom_url: str | None
    usaspending_recipient: str | None
    role: str  # portfolio | comp

    @property
    def ref(self) -> EntityRef:
        return domain_ref(self.domain) if self.domain else gh_org_ref(self.github_org or "")


def capture_targets(session: Session, cfg: PortfolioConfig) -> list[CaptureTarget]:
    """Portfolio companies first, then comps (each company once), with config identifiers. Call after sync."""
    resolver = Resolver(session, provider=PROVIDER)
    out: list[CaptureTarget] = []
    seen: set[uuid.UUID] = set()
    rows: list[tuple[CompanyConfig | CompConfig, str]] = [(c, "portfolio") for c in cfg.companies]
    rows += [(comp, "comp") for c in cfg.companies for comp in c.comps]
    for item, role in rows:
        m = match_company(session, resolver, item, cfg.source.url)
        if m.company.id in seen:
            continue
        seen.add(m.company.id)
        out.append(CaptureTarget(
            company_id=m.company.id, name=item.name, website=item.website, domain=config_domain(item.website),
            github_org=item.github_org, ats=(item.ats.provider, item.ats.slug) if item.ats else None,
            newsroom_url=getattr(item, "newsroom_url", None), usaspending_recipient=item.usaspending_recipient,
            role=role,
        ))
    return out

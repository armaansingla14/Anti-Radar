"""On-demand ingestion: GitHub repo/org/query, a company website, or one X post.

Every call goes through ``radar.ops`` (and therefore ``pipeline.runner.run_provider``),
so each run is recorded in ``ingestion_runs``. Endpoints are sync — FastAPI runs them
in its threadpool — and rate-limited (10/min). User-supplied URLs are sanitized
(scheme/host/port/credentials, private IP literals) before any network access; the
providers additionally refuse non-public resolved addresses at fetch time.
"""

from __future__ import annotations

import ipaddress
import logging
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import ops
from radar.api.ratelimit import rate_limit
from radar.db import get_session
from radar.models import Company, Person, Repository, UserAction
from radar.providers.http import UnsafeURLError, sanitize_url
from radar.schemas.api import (
    IngestEntityRef,
    IngestGithubRequest,
    IngestOut,
    IngestRunOut,
    IngestUrlRequest,
    IngestXRequest,
)
from radar.services.presenters import resolve_entity

log = logging.getLogger("radar.api.ingest")
router = APIRouter(tags=["ingest"], dependencies=[Depends(rate_limit("ingest", 10))])
SessionDep = Annotated[Session, Depends(get_session)]


def run_out(run: Any) -> IngestRunOut:
    return IngestRunOut(
        provider=str(getattr(run, "provider", "") or ""), status=str(getattr(run, "status", "") or "unknown"),
        records_fetched=int(getattr(run, "records_fetched", 0) or 0),
        records_accepted=int(getattr(run, "records_accepted", 0) or 0),
        records_rejected=int(getattr(run, "records_rejected", 0) or 0),
        signals_created=int(getattr(run, "signals_created", 0) or 0),
        errors=[str(e)[:300] for e in (getattr(run, "errors", None) or [])][:10],
    )


def _ref(kind: str, ent: Any) -> IngestEntityRef:
    if kind == "repository":
        return IngestEntityRef(type=kind, id=str(ent.id), slug=ent.full_name, name=ent.full_name)
    return IngestEntityRef(type=kind, id=str(ent.id), slug=ent.slug, name=ent.name)


def _recompute() -> bool:
    try:
        ops.recompute()
        return True
    except Exception:
        log.exception("recompute after ingest failed")
        return False


def check_public_literal(url: str) -> None:
    """Reject private/loopback/link-local IP literals (incl. integer form) without DNS."""
    host = (urlparse(url).hostname or "").strip("[]")
    try:
        addr = ipaddress.ip_address(int(host)) if host.isdigit() else ipaddress.ip_address(host)
    except ValueError:
        return
    if not addr.is_global:
        raise UnsafeURLError(f"host not allowed: {host!r}")


def safe_url(url: str) -> str:
    try:
        clean = sanitize_url(url)
        check_public_literal(clean)
        return clean
    except UnsafeURLError as e:
        raise HTTPException(422, detail=f"unsafe URL: {e}") from None


def _audit(session: Session, action: str, **meta: Any) -> None:
    session.add(UserAction(action=action, meta=meta))


@router.post("/ingest/github", response_model=IngestOut)
def ingest_github(req: IngestGithubRequest, session: SessionDep) -> IngestOut:
    given = [k for k in ("repo", "org", "query") if getattr(req, k)]
    if len(given) != 1:
        raise HTTPException(422, detail="provide exactly one of: repo, org, query")
    if req.repo:
        run, _ = ops.ingest_github(repo=req.repo)
    elif req.org:
        run, _ = ops.ingest_github(org=req.org)
    else:
        run, _ = ops.ingest_github(queries=[req.query.strip()], per_query=req.limit, max_repos=req.limit)
    recomputed = _recompute()
    entity: IngestEntityRef | None = None
    session.expire_all()
    if req.repo:
        repo = session.scalar(select(Repository).where(func.lower(Repository.full_name) == req.repo.lower()))
        if repo is not None:
            c = session.get(Company, repo.company_id) if repo.company_id else None
            entity = _ref("company", c) if c else _ref("repository", repo)
    elif req.org:
        c = session.scalar(select(Company).where(func.lower(Company.github_org) == req.org.lower()))
        entity = _ref("company", c) if c else None
    _audit(session, "ingest_github", repo=req.repo, org=req.org, query=req.query, status=run.status)
    return IngestOut(run=run_out(run), entity=entity, recomputed=recomputed)


@router.post("/ingest/url", response_model=IngestOut)
def ingest_url(req: IngestUrlRequest, session: SessionDep) -> IngestOut:
    url = safe_url(req.url)
    name = (req.company_name or "").strip() or None
    run, _ = ops.ingest_url(url, company_name=name)
    recomputed = _recompute()
    session.expire_all()
    from radar.providers.domains import company_domain

    entity = None
    domain = company_domain(url)
    if domain:
        c = session.scalar(select(Company).where(func.lower(Company.domain) == domain))
        entity = _ref("company", c) if c else None
    _audit(session, "ingest_url", url=url, status=run.status)
    return IngestOut(run=run_out(run), entity=entity, info={"url": url, "domain": domain}, recomputed=recomputed)


def _entity_ref_for(ent: Company | Person):
    from radar.providers.base import EntityRef

    if isinstance(ent, Company):
        if ent.domain:
            return EntityRef(type="company", key=f"domain:{ent.domain.lower()}")
        if ent.github_org:
            return EntityRef(type="company", key=f"github_org:{ent.github_org.lower()}")
        if ent.x_handle:
            return EntityRef(type="company", key=f"x:{ent.x_handle.lower()}")
        return EntityRef(type="company", key=f"name:{ent.name}")
    if ent.github_handle:
        return EntityRef(type="person", key=f"github_user:{ent.github_handle.lower()}")
    if ent.x_handle:
        return EntityRef(type="person", key=f"x:{ent.x_handle.lower()}")
    return EntityRef(type="person", key=f"name:{ent.name}")


@router.post("/ingest/x", response_model=IngestOut)
def ingest_x(req: IngestXRequest, session: SessionDep) -> IngestOut:
    from radar.providers.x.manual import InvalidPostURL, parse_post_url

    try:
        parse_post_url(req.url)
    except InvalidPostURL as e:
        raise HTTPException(422, detail=f"invalid X post URL: {e}") from None
    target: Company | Person | None = None
    if req.entity_id:
        if not req.entity_type:
            raise HTTPException(422, detail="entity_type is required with entity_id")
        target = resolve_entity(session, Company if req.entity_type == "company" else Person, req.entity_id)
        if target is None:
            raise HTTPException(404, detail=f"unknown {req.entity_type}: {req.entity_id}")
    run, _, info = ops.ingest_x_manual(req.url, req.pasted_text, entity_ref=_entity_ref_for(target) if target else None)
    safe_info = {k: info[k] for k in ("text_source", "handle", "canonical_url", "signal_type", "status_id",
                                      "handle_corrected_from", "author_name", "error") if k in info}
    if info.get("error"):
        raise HTTPException(422, detail=f"nothing ingested: {info['error']}")
    recomputed = _recompute()
    session.expire_all()
    entity = None
    if target is not None:
        entity = _ref("company" if isinstance(target, Company) else "person", target)
    elif info.get("handle"):
        p = session.scalar(select(Person).where(func.lower(Person.x_handle) == str(info["handle"]).lower()))
        entity = _ref("person", p) if p else None
    _audit(session, "ingest_x", url=req.url, text_source=info.get("text_source"), status=run.status)
    return IngestOut(run=run_out(run), entity=entity, info=safe_info, recomputed=recomputed)

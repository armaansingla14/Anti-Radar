"""Run diligence for a company and persist the result; report its status.

``run_diligence`` = collect (live) or load a capture (replay) → analyse (pure) → persist artifacts, claims,
the ``ResearchReport`` (kind ``diligence``) and the run summary. A failure is recorded on the run
(status ``error``), never half-written as a complete report.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.diligence import METHOD_VERSION
from radar.diligence.analyze import Analysis, analyze
from radar.diligence.capture import Capture, default_path, dump, load
from radar.diligence.collect import collect
from radar.diligence.taxonomy import Relation
from radar.diligence.types import CompanyFacts
from radar.models import Company, DiligenceArtifact, DiligenceClaim, DiligenceRun, ResearchReport, ReviewDecision

log = logging.getLogger(__name__)

STALE_RUN_GRACE_S = 120  # a "running" row older than its budget plus this is treated as abandoned
DEFAULT_BUDGET_S = 180


class DiligenceError(ValueError):
    pass


def resolve_company(session: Session, ref: Company | uuid.UUID | str) -> Company | None:
    """Company by object, UUID, slug, GitHub org, domain or exact (case-insensitive) name."""
    if isinstance(ref, Company):
        return ref
    s = str(ref).strip()
    if not s:
        return None
    try:
        row = session.get(Company, uuid.UUID(s))
        if row is not None:
            return row
    except ValueError:
        pass
    rows = list(session.scalars(select(Company).where(or_(
        Company.slug == s.lower(), Company.github_org.ilike(s), Company.domain == s.lower(), Company.name.ilike(s),
    )).order_by(Company.is_demo, Company.slug).limit(5)))
    for pred in (lambda c: c.slug == s.lower(), lambda c: (c.github_org or "").lower() == s.lower(),
                 lambda c: c.domain == s.lower()):
        hit = next((c for c in rows if pred(c)), None)
        if hit is not None:
            return hit
    return rows[0] if rows else None


def company_header(company: Company) -> dict[str, Any]:
    return {"id": str(company.id), "slug": company.slug, "name": company.name, "website": company.website,
            "github": company.github_org}


def _persist(session: Session, run: DiligenceRun, company: Company, cap: Capture, analysis: Analysis,
             elapsed_ms: float) -> None:
    rows: dict[str, DiligenceArtifact] = {}
    for a in sorted(cap.artifacts, key=lambda a: a.key):
        row = DiligenceArtifact(run_id=run.id, key=a.key, kind=a.kind, publisher=a.publisher, url=a.url, title=a.title,
                                fetched_at=a.fetched_at, http_status=a.http_status, content_hash=a.content_hash,
                                text=a.text, data=a.data, flags=a.flags)
        session.add(row)
        rows[a.key] = row
    session.flush()
    for c in analysis.claims:
        src = rows.get(c.source_key)
        session.add(DiligenceClaim(
            run_id=run.id, company_id=company.id, ordinal=c.ordinal, claim_text=c.text, claim_type=c.claim_type,
            source_id=src.id if src else None, source_url=c.source_url, source_quote=c.source_quote,
            observed_at=c.observed_at, epistemic="COMPANY_CLAIM", testability=c.testability,
            verification_status=c.status, status_reason=c.status_reason, confidence=c.confidence,
            company_evidence=c.company_evidence,
            independent_evidence=[e for e in c.evidence if e["relation"] != Relation.CONTRADICTS
                                  and e["publisher"] != "company"],
            contradictory_evidence=[e for e in c.evidence if e["relation"] == Relation.CONTRADICTS],
            test_plan=c.test_plan, extracted_by=c.extracted_by,
        ))
    body = analysis.report.model_dump(mode="json")
    report = ResearchReport(
        kind="diligence", entity_type="company", entity_id=company.id, title=f"Technical Diligence — {company.name}",
        body=body, markdown=analysis.markdown, sources=body["sources"], model=analysis.llm.get("model"),
        generator=analysis.generator, status="complete", cost_usd=float(analysis.llm.get("cost_usd") or 0.0),
        is_demo=company.is_demo,
        trace=[{"type": "timing", "ms": round(elapsed_ms, 1)},
               {"type": "summary", "run_id": str(run.id), "mode": run.mode, "method_version": METHOD_VERSION,
                "artifacts": len(cap.artifacts), "claims": len(analysis.claims)},
               {"type": "llm_extraction", **analysis.llm}],
    )
    session.add(report)
    session.flush()
    run.report_id = report.id
    run.summary = {**analysis.summary(), "partial": cap.partial}
    run.sources_analyzed = len(cap.artifacts)
    run.evidence_coverage = analysis.evidence_coverage()


def run_diligence(session: Session, company_ref: Company | uuid.UUID | str, *, live: bool = True, provider=None,
                  capture_path: Path | None = None, budget_s: float = DEFAULT_BUDGET_S, capture: bool = False,
                  commit_progress: bool = False, fetcher=None) -> DiligenceRun:
    """Collect (or replay) and analyse; persist and return the run.

    ``provider`` enables optional LLM-assisted claim extraction (statuses always come from the rules).
    ``capture`` writes the collected artifacts to ``radar/demo/diligence/<slug>.json.gz`` (or ``capture_path``).
    ``commit_progress`` commits the "running" row first so concurrent readers see IN_PROGRESS (API use).
    """
    company = resolve_company(session, company_ref)
    if company is None:
        raise DiligenceError(f"no company matches {company_ref!r}")
    path = Path(capture_path) if capture_path else default_path(company.slug)
    if not live and not path.exists():
        raise DiligenceError(f"no capture file at {path}")
    t0 = time.perf_counter()
    run = DiligenceRun(company_id=company.id, status="running", mode="live" if live else "replay",
                       method_version=METHOD_VERSION, started_at=clock.wall_now(), is_demo=company.is_demo, trace=[])
    session.add(run)
    session.flush()
    if commit_progress:
        session.commit()
    try:
        # A savepoint: if anything fails, partial artifacts/claims vanish and only the errored run row remains.
        with session.begin_nested():
            if live:
                res = collect(session, company, budget_s=budget_s, fetcher=fetcher)
                cap = Capture(facts=res.facts, artifacts=res.artifacts, collected_at=res.collected_at,
                              trace=res.trace, partial=res.partial, method_version=METHOD_VERSION)
                if res.errors:
                    cap.trace.append({"type": "errors", "errors": res.errors})
            else:
                cap = load(path)
            analysis = analyze(cap.artifacts, cap.facts, as_of=cap.collected_at, company=company_header(company),
                               provider=provider, partial=cap.partial)
            elapsed = (time.perf_counter() - t0) * 1000
            _persist(session, run, company, cap, analysis, elapsed)
            run.collected_at = cap.collected_at
            run.trace = [{"type": "facts", "facts": cap.facts.to_dict()}, *cap.trace,
                         {"type": "timing", "ms": round(elapsed, 1)}]
            run.status = "complete"
            run.finished_at = clock.wall_now()
        if live and capture:
            dump(cap, path)
    except Exception as e:  # noqa: BLE001 — recorded on the run; callers read run.status / run.error
        log.exception("diligence run failed for %s", company.slug)
        run.status = "error"
        run.error = f"{type(e).__name__}: {e}"[:2000]
        run.finished_at = clock.wall_now()
    session.flush()
    return run


# ── status ───────────────────────────────────────────────────────────────────


def _is_stale(run: DiligenceRun) -> bool:
    started = run.started_at
    return started is not None and clock.wall_now() - started > timedelta(seconds=DEFAULT_BUDGET_S + STALE_RUN_GRACE_S)


def latest_run(session: Session, company_id: uuid.UUID, *, complete_only: bool = False) -> DiligenceRun | None:
    q = select(DiligenceRun).where(DiligenceRun.company_id == company_id)
    if complete_only:
        q = q.where(DiligenceRun.status == "complete")
    return session.scalar(q.order_by(DiligenceRun.started_at.desc(), DiligenceRun.id).limit(1))


def review_targets(session: Session, run: DiligenceRun) -> list[dict[str, str]]:
    """Items that need a human decision: CONTRADICTED claims and HIGH-rated risk categories."""
    out: list[dict[str, str]] = []
    for c in session.scalars(select(DiligenceClaim).where(DiligenceClaim.run_id == run.id,
                                                          DiligenceClaim.verification_status == "CONTRADICTED")
                             .order_by(DiligenceClaim.ordinal)):
        out.append({"target_type": "claim", "target_id": str(c.id), "label": f"Claim {c.ordinal}: {c.claim_text}"})
    report = session.get(ResearchReport, run.report_id) if run.report_id else None
    for u in (report.body or {}).get("underwriting", []) if report else []:
        if u.get("scale") == "risk" and u.get("rating") == "HIGH":
            out.append({"target_type": "underwriting", "target_id": f"{run.id}:{u['key']}",
                        "label": f"{u['title']}: {u['headline']}"})
    return out


def diligence_status(session: Session, company_id: uuid.UUID) -> dict[str, Any]:
    """NOT_RUN | IN_PROGRESS | COMPLETE | NEEDS_REVIEW for the company's latest diligence."""
    latest = latest_run(session, company_id)
    if latest is not None and latest.status == "running" and not _is_stale(latest):
        return {"status": "IN_PROGRESS", "run_id": str(latest.id), "finished_at": None, "counts": {},
                "pending_review": [], "started_at": latest.started_at.isoformat() if latest.started_at else None}
    run = latest_run(session, company_id, complete_only=True)
    if run is None:
        out: dict[str, Any] = {"status": "NOT_RUN", "run_id": None, "finished_at": None, "counts": {},
                               "pending_review": []}
        if latest is not None and latest.status == "error":
            out["last_error"] = latest.error
        return out
    targets = review_targets(session, run)
    reviewed: set[tuple[str, str]] = set()
    if targets:
        ids = [t["target_id"] for t in targets]
        reviewed = {(r.target_type, r.target_id) for r in session.scalars(
            select(ReviewDecision).where(ReviewDecision.target_id.in_(ids)))}
    pending = [t for t in targets if (t["target_type"], t["target_id"]) not in reviewed]
    return {"status": "NEEDS_REVIEW" if pending else "COMPLETE", "run_id": str(run.id),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "counts": (run.summary or {}).get("by_status", {}), "pending_review": pending}


def facts_of(run: DiligenceRun) -> CompanyFacts | None:
    d = next((t.get("facts") for t in run.trace or [] if t.get("type") == "facts"), None)
    return CompanyFacts.from_dict(d) if d else None

"""Read model for the Diligence workspace API: index, workspace, single claim, reviews.

Responses are plain JSON-serialisable dicts with deterministic ordering (stable for snapshot export). Artifact
text is never returned — only source references.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.diligence import METHOD_VERSION
from radar.diligence.run import diligence_status, latest_run
from radar.diligence.taxonomy import STATUS_LABELS, definitions
from radar.diligence.types import parse_dt
from radar.models import Company, DiligenceClaim, DiligenceRun, ResearchReport, ReviewDecision
from radar.services.presenters import latest_scores, topic_tags

MAX_EVIDENCE_OUT = 25
MAX_CANDIDATES = 20
TARGET_TYPES = ("claim", "underwriting", "report", "failure")
DECISIONS = ("ACCEPT", "QUESTION", "REJECT", "NEEDS_VERIFICATION")


def _iso(d) -> str | None:
    return d.isoformat() if d else None


def company_ref(company: Company, domain: str | None = None) -> dict[str, Any]:
    return {"id": str(company.id), "slug": company.slug, "name": company.name, "website": company.website,
            "domain": company.domain, "github_org": company.github_org, "technical_domain": domain,
            "thesis_tags": sorted(company.thesis_tags or []), "is_demo": company.is_demo}


def _domains(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    tags = topic_tags(session, ids, limit=1) if ids else {}
    return {i: (tags.get(i) or [None])[0].label if tags.get(i) else None for i in ids}


def run_summary(run: DiligenceRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    s = dict(run.summary or {})
    return {"id": str(run.id), "status": run.status, "mode": run.mode, "method_version": run.method_version,
            "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
            "collected_at": _iso(run.collected_at), "sources_analyzed": run.sources_analyzed,
            "evidence_coverage": run.evidence_coverage, "summary": s,
            "report_id": str(run.report_id) if run.report_id else None, "error": run.error}


def index(session: Session, *, include_demo: bool = False) -> dict[str, Any]:
    """Companies with a diligence workspace, and Radar candidates that have not been run (top by ANTI score)."""
    run_company_ids = list(session.scalars(select(DiligenceRun.company_id).distinct()))
    companies = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(run_company_ids)))} \
        if run_company_ids else {}
    if not include_demo:
        companies = {k: v for k, v in companies.items() if not v.is_demo}
    doms = _domains(session, list(companies))
    workspaces = []
    for cid, c in companies.items():
        st = diligence_status(session, cid)
        run = latest_run(session, cid, complete_only=True) or latest_run(session, cid)
        workspaces.append({"company": company_ref(c, doms.get(cid)), "status": st["status"],
                           "run": run_summary(run), "finished_at": st.get("finished_at"),
                           "pending_review": st.get("pending_review", []), "last_error": st.get("last_error")})
    workspaces.sort(key=lambda w: (w["finished_at"] is None, -(_ts(w["finished_at"])), w["company"]["slug"]))

    q = select(Company.id).where(Company.dismissed.is_(False),
                                 (Company.website.is_not(None)) | (Company.github_org.is_not(None)))
    if not include_demo:
        q = q.where(Company.is_demo.is_(False))
    if run_company_ids:
        q = q.where(Company.id.not_in(run_company_ids))
    ids = list(session.scalars(q))
    scores = latest_scores(session, ids)
    top = sorted(ids, key=lambda i: (-(scores.get(i) or -1.0), str(i)))[:MAX_CANDIDATES]
    rows = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(top)))} if top else {}
    doms = _domains(session, top)
    candidates = [{"company": company_ref(rows[i], doms.get(i)),
                   "score": round(scores[i], 1) if i in scores else None} for i in top if i in rows]
    candidates.sort(key=lambda c: (-(c["score"] if c["score"] is not None else -1.0), c["company"]["slug"]))
    return {"workspaces": workspaces, "candidates": candidates}


def _ts(iso_s: str | None) -> float:
    d = parse_dt(iso_s)
    return d.timestamp() if d else 0.0


def reviews_for(session: Session, target_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    if not target_ids:
        return out
    for r in session.scalars(select(ReviewDecision).where(ReviewDecision.target_id.in_(target_ids))
                             .order_by(ReviewDecision.created_at.desc(), ReviewDecision.id)):
        out.setdefault(r.target_id, []).append(review_out(r))
    return out


def review_out(r: ReviewDecision) -> dict[str, Any]:
    return {"id": str(r.id), "target_type": r.target_type, "target_id": r.target_id, "decision": r.decision,
            "note": r.note, "reviewer": r.reviewer, "created_at": _iso(r.created_at)}


def _claim_out(row: DiligenceClaim, body_claim: dict[str, Any] | None, reviews: list[dict[str, Any]]
               ) -> dict[str, Any]:
    b = body_claim or {}
    return {
        "id": str(row.id), "ordinal": row.ordinal, "text": row.claim_text, "claim_type": row.claim_type,
        "source_id": b.get("source_id"), "source_url": row.source_url, "source_quote": row.source_quote,
        "observed_at": _iso(row.observed_at), "epistemic": row.epistemic, "testability": row.testability,
        "status": row.verification_status, "status_label": STATUS_LABELS.get(row.verification_status,
                                                                             row.verification_status),
        "status_reason": row.status_reason, "rule": b.get("rule", ""), "confidence": row.confidence,
        "extracted_by": row.extracted_by,
        "company_evidence": (b.get("company_evidence") or [])[:MAX_EVIDENCE_OUT],
        "independent_evidence": (b.get("independent_evidence") or [])[:MAX_EVIDENCE_OUT],
        "contradictory_evidence": (b.get("contradictory_evidence") or [])[:MAX_EVIDENCE_OUT],
        "test_plan": row.test_plan,
        "reviews": reviews,
    }


def _empty(company: Company, domain: str | None, status: dict[str, Any]) -> dict[str, Any]:
    return {"company": company_ref(company, domain), "status": status,
            "header": {"date": None, "status": status["status"], "evidence_coverage": None, "sources_analyzed": 0,
                       "last_updated": None, "technical_domain": domain},
            "executive_summary": None, "claims": [], "underwriting": [], "contradictions": [], "risks": [],
            "unknowns": [], "questions": [], "sources": [], "sections": {}, "report_id": None, "run": None,
            "definitions": definitions(), "method_version": METHOD_VERSION}


def workspace(session: Session, company: Company) -> dict[str, Any]:
    domain = _domains(session, [company.id]).get(company.id)
    status = diligence_status(session, company.id)
    run = latest_run(session, company.id, complete_only=True)
    if run is None:
        return _empty(company, domain, status)
    report = session.get(ResearchReport, run.report_id) if run.report_id else None
    body = (report.body if report else None) or {}
    by_ord = {c["ordinal"]: c for c in body.get("claims") or []}
    rows = list(session.scalars(select(DiligenceClaim).where(DiligenceClaim.run_id == run.id)
                                .order_by(DiligenceClaim.ordinal)))
    uw = body.get("underwriting") or []
    uw_ids = [f"{run.id}:{u['key']}" for u in uw]
    revs = reviews_for(session, [str(r.id) for r in rows] + uw_ids + ([str(report.id)] if report else []))
    underwriting = [{**u, "target_id": f"{run.id}:{u['key']}", "reviews": revs.get(f"{run.id}:{u['key']}", [])}
                    for u in uw]
    trace = run.trace or []
    fetches = [t for t in trace if t.get("type") == "fetch"]
    sections = {k: body.get(k) for k in ("technical_differentiation", "architecture", "open_source_health", "security",
                                         "dependencies", "infrastructure", "ai_systems", "benchmarks")}
    if sections.get("dependencies"):
        deps = dict(sections["dependencies"])
        deps["dependencies"] = (deps.get("dependencies") or [])[:100]
        sections["dependencies"] = deps
    return {
        "company": company_ref(company, domain),
        "status": status,
        "header": {"date": body.get("date"), "status": status["status"], "evidence_coverage": run.evidence_coverage,
                   "sources_analyzed": run.sources_analyzed, "last_updated": _iso(run.finished_at),
                   "technical_domain": domain},
        "executive_summary": body.get("executive_summary"),
        "claims": [_claim_out(r, by_ord.get(r.ordinal), revs.get(str(r.id), [])) for r in rows],
        "underwriting": underwriting,
        "contradictions": body.get("contradictory_evidence") or [],
        "risks": body.get("key_risks") or [],
        "unknowns": body.get("unknowns") or [],
        "questions": body.get("questions_for_founders") or [],
        "sources": body.get("sources") or [],
        "sections": sections,
        "report_id": str(report.id) if report else None,
        "report_reviews": revs.get(str(report.id), []) if report else [],
        "run": {**(run_summary(run) or {}),
                "trace_summary": {"requests": len(fetches),
                                  "errors": sum(1 for t in fetches if t.get("error")),
                                  "refused": sum(1 for t in fetches if str(t.get("error") or "").startswith("refused")),
                                  "partial": bool((run.summary or {}).get("partial")),
                                  "timing_ms": next((t.get("ms") for t in trace if t.get("type") == "timing"), None)}},
        "definitions": definitions(),
        "method_version": run.method_version,
    }


def claim_detail(session: Session, company: Company, ordinal: int) -> dict[str, Any] | None:
    run = latest_run(session, company.id, complete_only=True)
    if run is None:
        return None
    row = session.scalar(select(DiligenceClaim).where(DiligenceClaim.run_id == run.id,
                                                      DiligenceClaim.ordinal == ordinal))
    if row is None:
        return None
    report = session.get(ResearchReport, run.report_id) if run.report_id else None
    body_claim = next((c for c in ((report.body or {}).get("claims") or []) if c.get("ordinal") == ordinal), None) \
        if report else None
    out = _claim_out(row, body_claim, reviews_for(session, [str(row.id)]).get(str(row.id), []))
    out["run_id"] = str(run.id)
    out["sources"] = list((report.body or {}).get("sources") or []) if report else []
    return out


class ReviewError(ValueError):
    pass


def create_review(session: Session, *, target_type: str, target_id: str, decision: str, note: str = "",
                  reviewer: str = "analyst") -> ReviewDecision:
    """Store a human decision. Claim / report / underwriting targets must exist."""
    if target_type not in TARGET_TYPES or decision not in DECISIONS:
        raise ReviewError("invalid target_type or decision")
    tid = target_id.strip()
    if target_type == "claim":
        if _uuid(tid) is None or session.get(DiligenceClaim, _uuid(tid)) is None:
            raise LookupError("claim not found")
    elif target_type == "report":
        rep = session.get(ResearchReport, _uuid(tid)) if _uuid(tid) else None
        if rep is None:
            raise LookupError("report not found")
    elif target_type == "underwriting":
        run_part, _, key = tid.partition(":")
        run = session.get(DiligenceRun, _uuid(run_part)) if _uuid(run_part) else None
        rep = session.get(ResearchReport, run.report_id) if run and run.report_id else None
        if rep is None or key not in {u.get("key") for u in (rep.body or {}).get("underwriting") or []}:
            raise LookupError("underwriting category not found")
    r = ReviewDecision(target_type=target_type, target_id=tid[:120], decision=decision, note=(note or "")[:2000],
                       reviewer=(reviewer or "analyst")[:120])
    session.add(r)
    session.flush()
    session.refresh(r)
    return r


def list_reviews(session: Session, *, target_type: str | None = None, target_id: str | None = None,
                 limit: int = 100) -> list[dict[str, Any]]:
    q = select(ReviewDecision)
    if target_type:
        q = q.where(ReviewDecision.target_type == target_type)
    if target_id:
        q = q.where(ReviewDecision.target_id == target_id)
    return [review_out(r) for r in session.scalars(q.order_by(ReviewDecision.created_at.desc(), ReviewDecision.id)
                                                   .limit(limit))]


def _uuid(s: str) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(s))
    except ValueError:
        return None

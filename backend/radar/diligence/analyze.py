"""The analysis pipeline: artifacts in, structured report out. Pure — no database, no network, no clock.

All time-relative judgements (push recency, release cadence, dependency age) are made relative to ``as_of``,
the moment the artifacts were collected, so a replayed capture produces the same report on any later day.
The only optional non-deterministic step is LLM-assisted claim *extraction* (never status assignment), used
only when a live provider is passed in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from radar.diligence import METHOD_VERSION
from radar.diligence.ai_systems import ai_review
from radar.diligence.contradictions import detect
from radar.diligence.deps import analyze_dependencies
from radar.diligence.evidence import MAX_EVIDENCE_PER_CLAIM, Context, attach_evidence, sort_evidence
from radar.diligence.extract import extract_claims, extract_with_llm, merge_and_rank
from radar.diligence.infra import infra_review
from radar.diligence.oss import oss_health
from radar.diligence.questions import generate_questions
from radar.diligence.report import DiligenceReport, build_report, to_markdown
from radar.diligence.rules import apply_rules
from radar.diligence.security import security_review
from radar.diligence.taxonomy import STATUS_ORDER, ClaimType, Publisher, Testability
from radar.diligence.testplan import build_test_plan
from radar.diligence.types import ArtifactDraft, ClaimDraft, CompanyFacts
from radar.diligence.underwriting import underwrite


@dataclass
class Analysis:
    claims: list[ClaimDraft]
    conflicts: list[dict[str, Any]]
    underwriting: list[dict[str, Any]]
    questions: list[dict[str, Any]]
    report: DiligenceReport
    markdown: str
    llm: dict[str, Any] = field(default_factory=dict)

    @property
    def generator(self) -> str:
        return "llm" if any(c.extracted_by == "llm" for c in self.claims) else "rules"

    def summary(self) -> dict[str, Any]:
        """Counts for DiligenceRun.summary (stable key order)."""
        by_status = {s: sum(1 for c in self.claims if c.status == s) for s in STATUS_ORDER}
        by_type = {t.value: n for t in ClaimType if (n := sum(1 for c in self.claims if c.claim_type == t))}
        by_test = {t.value: n for t in Testability if (n := sum(1 for c in self.claims if c.testability == t))}
        return {"claims": len(self.claims), "by_status": by_status, "by_type": by_type, "by_testability": by_test,
                "conflicts": len(self.conflicts), "questions": len(self.questions),
                "underwriting": {u["key"]: u["rating"] for u in self.underwriting}}

    def evidence_coverage(self) -> float | None:
        """Share of claims with at least one non-company supporting item."""
        if not self.claims:
            return None
        n = sum(1 for c in self.claims if any(e["relation"] == "supports" and e["publisher"] != Publisher.COMPANY
                                              and not e.get("gap") for e in c.evidence))
        return round(n / len(self.claims), 3)


def analyze(artifacts: list[ArtifactDraft], facts: CompanyFacts, *, as_of: datetime, company: dict[str, Any],
            provider=None, partial: bool = False) -> Analysis:
    arts = sorted(artifacts, key=lambda a: a.key)
    by_key = {a.key: a for a in arts}
    deps = analyze_dependencies(arts, as_of)
    ctx = Context.build(arts, facts, as_of, deps)

    claims = extract_claims(arts)
    llm: dict[str, Any] = {"used": False, "model": None, "rejected": 0, "cost_usd": 0.0, "error": None}
    if provider is not None:
        extra, llm = extract_with_llm(provider, arts)
        if extra:
            claims = merge_and_rank(claims + extra)

    attach_evidence(claims, ctx)
    conflicts = detect(claims, ctx)
    for c in claims:
        c.evidence = sort_evidence(c.evidence)[:MAX_EVIDENCE_PER_CLAIM]
    apply_rules(claims)
    packages = {str(a.data.get("name", "")).lower(): a for a in arts if a.kind == "package"}
    for c in claims:
        c.test_plan = build_test_plan(c, source=by_key.get(c.source_key), packages=packages)

    oss = oss_health(arts, facts, as_of)
    sec = security_review(arts, deps)
    infra = infra_review(arts, deps)
    ai = ai_review(arts, deps, facts.thesis_tags)
    uw = underwrite(claims, arts, conflicts=conflicts, oss=oss, deps=deps, sec=sec, infra=infra, ai=ai)
    questions = generate_questions(claims, conflicts, oss=oss, deps=deps, sec=sec, ai=ai, product=facts.name)

    kinds: dict[str, int] = {}
    pubs: dict[str, int] = {}
    for a in arts:
        kinds[a.kind] = kinds.get(a.kind, 0) + 1
        pubs[a.publisher] = pubs.get(a.publisher, 0) + 1
    collection = {"artifacts": len(arts), "by_kind": dict(sorted(kinds.items())), "by_publisher": dict(sorted(pubs.items())),
                  "partial": partial, "flagged_artifacts": sorted(a.key for a in arts if a.flags),
                  "llm_extraction": llm}
    report = build_report(company=company, as_of=as_of, method_version=METHOD_VERSION, artifacts=arts, claims=claims,
                          conflicts=conflicts, underwriting=uw, oss=oss, deps=deps, sec=sec, infra=infra, ai=ai,
                          questions=questions, collection=collection)
    return Analysis(claims=claims, conflicts=conflicts, underwriting=uw, questions=questions, report=report,
                    markdown=to_markdown(report), llm=llm)

"""The structured Diligence Report (validated JSON) and its deterministic markdown rendering.

The executive summary is assembled from counts and rule outcomes, not written by a model. Sources ``S1…`` are
assigned in order of first citation (claims, conflicts, findings, underwriting, questions) and use the same
shape as ``ResearchReport.sources``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

from radar.diligence.taxonomy import (
    CONFLICT_LABEL,
    PUBLISHER_LABELS,
    STATUS_LABELS,
    STATUS_ORDER,
    Publisher,
    Relation,
)
from radar.diligence.taxonomy import VerificationStatus as VS
from radar.diligence.types import ArtifactDraft, ClaimDraft, iso

DISCLAIMER = ("Evidence assessment of public technical claims — not an investment recommendation. Statuses come from "
              "documented rules (R1–R6) over the evidence listed; what could not be established is marked unknown.")


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportCompany(_M):
    id: str | None
    slug: str
    name: str
    website: str | None
    github: str | None


class EvidenceRef(_M):
    artifact_key: str
    source_id: str | None
    url: str
    title: str
    publisher: str
    publisher_label: str
    kind: str
    relation: str
    strength: str
    verifies: bool
    quote: str
    observed_at: str | None
    matcher: str
    note: str
    gap: bool = False


class ReportClaim(_M):
    ordinal: int
    text: str
    claim_type: str
    source_id: str | None
    source_url: str
    source_quote: str
    observed_at: str | None
    testability: str
    status: str
    status_label: str
    status_reason: str
    rule: str
    confidence: str
    extracted_by: str
    company_evidence: list[EvidenceRef]
    independent_evidence: list[EvidenceRef]
    contradictory_evidence: list[EvidenceRef]
    test_plan: dict[str, Any] | None


class ExecutiveSummary(_M):
    evidence_reviewed: int
    material_claims: int
    counts: dict[str, int]
    strongest_evidence: str
    largest_uncertainty: str
    critical_question: str | None
    text: str


class UnderwritingCategory(_M):
    key: str
    title: str
    scale: str
    rating: str
    headline: str
    known: list[str]
    inferred: list[str]
    unknown: list[str]
    evidence: list[dict[str, Any]]
    rule: str


class Conflict(_M):
    id: str
    kind: str
    label: str
    claim_ordinals: list[int]
    summary: str
    evidence: list[EvidenceRef]
    details: dict[str, Any]


class Risk(_M):
    title: str
    severity: str
    category: str
    detail: str
    url: str | None


class Question(_M):
    question: str
    reason: str
    claim_ordinals: list[int]
    evidence: list[dict[str, Any]]
    priority: int


class Source(_M):
    id: str
    url: str
    title: str
    source_type: str
    publisher: str
    fetched_at: str | None
    artifact_key: str


class DiligenceReport(_M):
    company: ReportCompany
    date: str
    as_of: str
    method_version: str
    status: str
    disclaimer: str
    executive_summary: ExecutiveSummary
    claims: list[ReportClaim]
    technical_differentiation: dict[str, Any]
    architecture: dict[str, Any]
    open_source_health: dict[str, Any]
    security: dict[str, Any]
    dependencies: dict[str, Any]
    infrastructure: dict[str, Any]
    ai_systems: dict[str, Any] | None
    benchmarks: dict[str, Any]
    underwriting: list[UnderwritingCategory]
    contradictory_evidence: list[Conflict]
    key_risks: list[Risk]
    unknowns: list[str]
    questions_for_founders: list[Question]
    sources: list[Source]
    collection: dict[str, Any]


# ── building ─────────────────────────────────────────────────────────────────


class _Sources:
    def __init__(self, artifacts: dict[str, ArtifactDraft]) -> None:
        self.artifacts = artifacts
        self.ids: dict[str, str] = {}

    def cite(self, key: str | None) -> str | None:
        if not key or key not in self.artifacts:
            return None
        if key not in self.ids:
            self.ids[key] = f"S{len(self.ids) + 1}"
        return self.ids[key]

    def out(self) -> list[dict[str, Any]]:
        res = []
        for key, sid in self.ids.items():
            a = self.artifacts[key]
            res.append({"id": sid, "url": a.url, "title": a.title or a.key, "source_type": a.kind,
                        "publisher": a.publisher, "fetched_at": iso(a.fetched_at), "artifact_key": a.key})
        return res


def _ref(e: dict[str, Any], src: _Sources) -> dict[str, Any]:
    return {**{k: e.get(k) for k in ("artifact_key", "url", "title", "publisher", "kind", "relation", "strength",
                                     "quote", "observed_at", "matcher", "note")},
            "verifies": bool(e.get("verifies")), "gap": bool(e.get("gap")),
            "source_id": src.cite(e.get("artifact_key")),
            "publisher_label": PUBLISHER_LABELS.get(e.get("publisher", ""), str(e.get("publisher", "")).upper())}


def _claim(c: ClaimDraft, src: _Sources) -> dict[str, Any]:
    sid = src.cite(c.source_key)
    company = [_ref(e, src) for e in c.company_evidence]
    contra = [_ref(e, src) for e in c.evidence if e["relation"] == Relation.CONTRADICTS]
    indep = [_ref(e, src) for e in c.evidence if e["relation"] != Relation.CONTRADICTS
             and e["publisher"] != Publisher.COMPANY]
    return {"ordinal": c.ordinal, "text": c.text, "claim_type": c.claim_type, "source_id": sid,
            "source_url": c.source_url, "source_quote": c.source_quote, "observed_at": iso(c.observed_at),
            "testability": c.testability, "status": c.status, "status_label": STATUS_LABELS.get(c.status, c.status),
            "status_reason": c.status_reason, "rule": c.rule, "confidence": c.confidence,
            "extracted_by": c.extracted_by, "company_evidence": company, "independent_evidence": indep,
            "contradictory_evidence": contra, "test_plan": c.test_plan}


_SEV = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _risks(underwriting: list[dict], conflicts: list[dict], sec: dict) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for u in underwriting:
        if u["scale"] == "risk" and u["rating"] in ("HIGH", "MODERATE"):
            out.append({"title": f"{u['title']}: {u['headline']}", "severity": "HIGH" if u["rating"] == "HIGH" else "MEDIUM",
                        "category": u["key"], "detail": u["rule"],
                        "url": (u["evidence"][0].get("url") if u["evidence"] else None) or None})
    for c in conflicts:
        if any(c["summary"] in r["title"] for r in out):
            continue  # already stated by an underwriting headline
        out.append({"title": f"{c['label']}: {c['summary']}", "severity": "HIGH" if c["label"] == CONFLICT_LABEL else "MEDIUM",
                    "category": "CLAIM_VERIFICATION", "detail": f"Claims {', '.join(map(str, c['claim_ordinals']))}"
                    if c["claim_ordinals"] else "", "url": (c["evidence"][0]["url"] if c["evidence"] else None) or None})
    for f in sec.get("findings") or []:
        if f["severity"] == "HIGH":
            out.append({"title": f["title"], "severity": "HIGH", "category": "SECURITY", "detail": f["why_it_matters"],
                        "url": f["evidence"]["url"] or None})
    uniq = {r["title"]: r for r in out}
    return sorted(uniq.values(), key=lambda r: (_SEV[r["severity"]], r["category"], r["title"]))[:15]


def _unknowns(claims: list[ClaimDraft], underwriting: list[dict]) -> list[str]:
    out: list[str] = []
    for c in claims:
        if c.status == VS.UNTESTABLE:
            out.append(f"Claim {c.ordinal} cannot be evaluated from public evidence: “{c.text}”")
        elif c.status == VS.UNVERIFIED:
            out.append(f"Claim {c.ordinal} rests only on the company's statement: “{c.text}”")
    for u in underwriting:
        out.extend(x for x in u["unknown"] if not x.startswith("Claim "))  # per-claim lines are listed above
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))][:25]


def _summary(claims: list[ClaimDraft], conflicts: list[dict], questions: list[dict], n_artifacts: int
             ) -> dict[str, Any]:
    counts = {s: sum(1 for c in claims if c.status == s) for s in STATUS_ORDER}
    best = sorted([c for c in claims if c.status in (VS.VERIFIED, VS.SUPPORTED)],
                  key=lambda c: (c.status != VS.VERIFIED, -c.materiality, c.ordinal))
    if best:
        c = best[0]
        lead = next((e for e in c.evidence if e["relation"] == Relation.SUPPORTS and e["publisher"] != Publisher.COMPANY), None)
        strongest = f"Claim {c.ordinal} ({STATUS_LABELS[c.status].lower()}): {c.text}" + (f" — {lead['note']}" if lead else "")
    else:
        strongest = "No claim has verifying or multiple independent supporting evidence."
    if conflicts:
        largest = f"{conflicts[0]['label']}: {conflicts[0]['summary']}"
    else:
        open_ = sorted([c for c in claims if c.status in (VS.UNVERIFIED, VS.UNTESTABLE, VS.PARTIALLY_SUPPORTED)],
                       key=lambda c: (-c.materiality, c.ordinal))
        largest = (f"Claim {open_[0].ordinal} ({STATUS_LABELS[open_[0].status].lower()}): {open_[0].text}"
                   if open_ else "None identified from public evidence.")
    parts = [f"{counts[s]} {STATUS_LABELS[s].lower()}" for s in STATUS_ORDER if counts[s]]
    text = (f"{n_artifacts} public artifacts reviewed; {len(claims)} material claims"
            + (f": {', '.join(parts)}." if parts else ".")
            + (f" {len(conflicts)} item(s) of contradictory evidence need resolution." if conflicts else ""))
    return {"evidence_reviewed": n_artifacts, "material_claims": len(claims), "counts": counts,
            "strongest_evidence": strongest, "largest_uncertainty": largest,
            "critical_question": questions[0]["question"] if questions else None, "text": text}


def build_report(*, company: dict[str, Any], as_of, method_version: str, artifacts: list[ArtifactDraft],
                 claims: list[ClaimDraft], conflicts: list[dict], underwriting: list[dict], oss: dict, deps: dict,
                 sec: dict, infra: dict, ai: dict | None, questions: list[dict], collection: dict[str, Any]
                 ) -> DiligenceReport:
    src = _Sources({a.key: a for a in artifacts})
    claims_out = [_claim(c, src) for c in claims]
    conflicts_out = [{**c, "evidence": [_ref(e, src) for e in c["evidence"]]} for c in conflicts]
    for f in sec.get("findings") or []:
        f["evidence"]["source_id"] = src.cite(f["evidence"].get("artifact_key"))
    for u in underwriting:
        for e in u["evidence"]:
            e["source_id"] = src.cite(e.get("artifact_key"))
    for q in questions:
        for e in q["evidence"]:
            e["source_id"] = src.cite(e.get("artifact_key"))
    by_key = {u["key"]: u for u in underwriting}
    perf = [c for c in claims_out if c["claim_type"] == "PERFORMANCE"]
    bench_uw = by_key.get("BENCHMARK_EVIDENCE", {})
    report = {
        "company": company,
        "date": as_of.date().isoformat(),
        "as_of": as_of.isoformat(),
        "method_version": method_version,
        "status": "complete" if not collection.get("partial") else "complete_partial_collection",
        "disclaimer": DISCLAIMER,
        "executive_summary": _summary(claims, conflicts, questions, len(artifacts)),
        "claims": claims_out,
        "technical_differentiation": by_key.get("TECHNICAL_DIFFERENTIATION", {}),
        "architecture": {**by_key.get("ARCHITECTURE", {}), "languages": {
            a.data.get("full_name"): a.data.get("bytes") for a in artifacts if a.kind == "languages"}},
        "open_source_health": oss,
        "security": sec,
        "dependencies": deps,
        "infrastructure": infra,
        "ai_systems": ai,
        "benchmarks": {
            "rating": bench_uw.get("rating", "UNKNOWN"),
            "performance_claims": [{"ordinal": c["ordinal"], "text": c["text"], "status": c["status"],
                                    "status_label": c["status_label"], "has_test_plan": c["test_plan"] is not None}
                                   for c in perf],
            "baselines": [c["details"] for c in conflicts if c["kind"] == "stale_benchmark_baseline"],
            "notes": bench_uw.get("known", []),
        },
        "underwriting": underwriting,
        "contradictory_evidence": conflicts_out,
        "key_risks": _risks(underwriting, conflicts, sec),
        "unknowns": _unknowns(claims, underwriting),
        "questions_for_founders": questions,
        "sources": src.out(),
        "collection": collection,
    }
    return DiligenceReport.model_validate(report)


# ── markdown ─────────────────────────────────────────────────────────────────


def _cite(sid: str | None) -> str:
    return f" [{sid}]" if sid else ""


def _cell(s: str) -> str:
    return (s or "").replace("|", "\\|").replace("\n", " ")


def to_markdown(r: DiligenceReport) -> str:
    """Deterministic rendering of the report (same report in, byte-identical markdown out)."""
    es = r.executive_summary
    L: list[str] = [f"# Technical Diligence — {r.company.name}", "",
                    f"*{r.date} · method {r.method_version} · {es.evidence_reviewed} artifacts reviewed*", "",
                    f"> {r.disclaimer}", "", "## Executive summary", "", es.text, "",
                    f"- **Strongest evidence:** {es.strongest_evidence}",
                    f"- **Largest uncertainty:** {es.largest_uncertainty}"]
    if es.critical_question:
        L.append(f"- **Critical question:** {es.critical_question}")
    L += ["", "## Claim ledger", "", "| # | Claim | Type | Status | Testability | Confidence |",
          "|---|---|---|---|---|---|"]
    for c in r.claims:
        L.append(f"| {c.ordinal} | {_cell(c.text)}{_cite(c.source_id)} | {c.claim_type} | {c.status_label} | "
                 f"{c.testability} | {c.confidence} |")
    for c in r.claims:
        L += ["", f"### Claim {c.ordinal}: {c.status_label}", "", f"> {c.source_quote}{_cite(c.source_id)}", "",
              f"*{c.status_reason}*"]
        for title, items in (("Independent and public-artifact evidence", c.independent_evidence),
                             ("Contradictory evidence", c.contradictory_evidence)):
            if items:
                L += ["", f"**{title}**"]
                for e in items:
                    tag = "NO EVIDENCE" if e.gap else f"{e.publisher_label} · {e.relation} · {e.strength}"
                    L.append(f"- {e.note} ({tag}){_cite(e.source_id)}")
        if c.test_plan:
            tp = c.test_plan
            L += ["", "**Validation plan** (not executed)", f"- Hypothesis: {tp['hypothesis']}"]
            if tp.get("baseline"):
                L.append(f"- Baseline: {tp['baseline']['name']} — {tp['baseline']['version_guidance']}")
            if tp.get("targets"):
                L.append(f"- Targets: {'; '.join(tp['targets'])}")
            if tp.get("hardware"):
                L.append(f"- Hardware: {'; '.join(tp['hardware'])}")
            if tp.get("models"):
                L.append(f"- Models: {'; '.join(tp['models'])}")
            L.append(f"- Metrics: {'; '.join(tp['metrics'])}")
            L += [f"- Pass: {p}" for p in tp["pass_criteria"]]
    if r.contradictory_evidence:
        L += ["", "## Contradictory evidence", ""]
        for cf in r.contradictory_evidence:
            cites = "".join(_cite(e.source_id) for e in cf.evidence)
            claims = f" (claims {', '.join(map(str, cf.claim_ordinals))})" if cf.claim_ordinals else ""
            L.append(f"- **{cf.label}**{claims}: {cf.summary}{cites}")
    L += ["", "## Technical underwriting", "", "| Category | Scale | Rating | Headline |", "|---|---|---|---|"]
    for u in r.underwriting:
        L.append(f"| {u.title} | {u.scale} | {u.rating} | {_cell(u.headline)} |")
    for u in r.underwriting:
        L += ["", f"### {u.title} — {u.rating}", "", f"*Rule: {u.rule}*"]
        for label, items in (("Known", u.known), ("Inferred", u.inferred), ("Unknown", u.unknown)):
            if items:
                L.append(f"- **{label}:** " + " ".join(items))
    sec = r.security
    if sec.get("findings"):
        L += ["", "## Security findings (public artifacts only)", ""]
        for f in sec["findings"]:
            ev = f["evidence"]
            L.append(f"- **{f['id']} {f['severity']}** {f['title']} — {f['why_it_matters']}{_cite(ev.get('source_id'))}")
    if r.key_risks:
        L += ["", "## Key risks", ""] + [f"- **{k.severity}** {k.title}" for k in r.key_risks]
    if r.unknowns:
        L += ["", "## Unknowns", ""] + [f"- {u}" for u in r.unknowns]
    if r.questions_for_founders:
        L += ["", "## Questions for founders", ""]
        for i, q in enumerate(r.questions_for_founders, 1):
            cites = "".join(_cite(e.get("source_id")) for e in q.evidence)
            L.append(f"{i}. {q.question}{cites}  \n   *Why: {q.reason}*")
    L += ["", "## Sources", ""]
    for s in r.sources:
        L.append(f"- [{s.id}] {s.title} — {s.url} ({PUBLISHER_LABELS.get(s.publisher, s.publisher)}, "
                 f"fetched {(s.fetched_at or 'unknown')[:10]})")
    return "\n".join(L) + "\n"

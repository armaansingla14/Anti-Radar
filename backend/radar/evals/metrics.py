"""Per-report evaluation (stored in ``agent_run_evaluations``) and per-agent aggregates.

Nothing here is estimated: a metric whose denominator is zero, or whose input was never recorded, is ``None``
with a reason. Every aggregate has the shape ``{"value", "n", "reason"}``.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.agent.brief_schema import AntiBrief, AntiMemo, MeetingBrief
from radar.evals.checks import CheckResult, check_report, fallback_info
from radar.evals.taxonomy import AGENTS, EVALUATOR_VERSION, ReviewStatus, agent_for_kind, agent_for_purpose
from radar.models import AgentFailure, AgentRunEvaluation, LLMCall, ResearchReport
from radar.schemas.api import MorningBrief
from radar.schemas.portfolio import PortfolioBriefBody, PortfolioReviewBody

SCHEMAS: dict[str, type[BaseModel]] = {"brief": AntiBrief, "memo": AntiMemo, "meeting_brief": MeetingBrief,
                                       "morning_brief": MorningBrief, "portfolio_review": PortfolioReviewBody,
                                       "portfolio_brief": PortfolioBriefBody}
TELEMETRY_ROWS = 20_000

METHODOLOGY: dict[str, str] = {
    "runs": "Stored reports of the agent's kind evaluated at the current evaluator version.",
    "success_rate": "Share of runs with status 'complete' whose body validates against the agent's output schema.",
    "structured_validity_rate": "Share of runs whose stored body validates against the output schema (Pydantic "
                                "model; diligence: DiligenceReport when available, else a structural check).",
    "error_rate": "Share of runs whose report status is not 'complete'.",
    "fallback_rate": "Share of runs where a live LLM was attempted and the deterministic template replaced it. A "
                     "run with no LLM configured is not a fallback. Not recorded for ANTI MEMO (null).",
    "citation_validity": "Delivered citations that point to a source registered for the report, over all delivered "
                         "citations (pooled). This measures existence, not semantic support. Delivered reports have "
                         "passed the citation guard, so this is normally 1.0; what the guard removed is reported "
                         "separately as guard_* flags.",
    "unsupported_claim_rate": "Delivered claims that are unsupported, over all delivered claims (pooled). Unsupported "
                              "= a FACT with no citation, a claim whose every citation is nonexistent, an "
                              "employment FACT resting only on event sources, or a funding claim without a funding "
                              "source. 'source may be unrelated' flags and guard interventions are not counted.",
    "source_coverage": "Mean, over runs with registered sources, of the share of registered sources that at least "
                       "one delivered claim cites.",
    "avg_sources_used": "Mean number of distinct registered sources cited per run.",
    "latency_p50_ms": "Median wall time of the run from its trace 'timing' entry (nearest rank). Runs without a "
                      "timing entry (generated before timing was recorded) are excluded, never estimated.",
    "latency_p95_ms": "95th percentile (nearest rank) of the same timing entries.",
    "avg_cost_usd": "Mean of the report's own cost_usd (LLM spend recorded by the agent; 0 for templates).",
    "human_review_pending": "Flags marked needs_human_review on runs not yet reviewed, plus OPEN failures owned by "
                            "the agent. A count; never null.",
    "guard_interventions": "Flags guard_removed_invalid_ids / guard_downgraded_fact: what the model produced before "
                           "the citation guard repaired it (from the trace). Kept apart from delivered metrics.",
    "llm_telemetry": "Per LLM purpose, from the llm_calls log (latest 20,000 calls): calls, ok rate, latency over "
                     "live calls (cache hits excluded), cost, cache-hit rate. LLM calls are not linked to reports.",
}


def metric(value: float | int | None, n: int, reason_if_none: str) -> dict[str, Any]:
    if value is None or n == 0:
        return {"value": None, "n": n, "reason": reason_if_none}
    return {"value": round(float(value), 4), "n": n, "reason": None}


def nearest_rank(values: Sequence[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    k = max(1, math.ceil(p / 100.0 * len(xs)))
    return xs[min(k, len(xs)) - 1]


def structured_valid(kind: str | None, body: Any) -> tuple[bool, str | None]:
    """(valid, note). The note explains an invalid body or a weaker (structural) check."""
    if kind == "diligence":
        try:
            from radar.diligence.report import DiligenceReport  # being written in parallel; optional
        except Exception:  # noqa: BLE001 — module absent or broken: fall back to a structural check
            DiligenceReport = None  # noqa: N806
        if DiligenceReport is not None:
            try:
                DiligenceReport.model_validate(body)
                return True, None
            except ValidationError as e:
                return False, f"DiligenceReport: {e.error_count()} validation error(s)"
            except Exception as e:  # noqa: BLE001
                return False, f"DiligenceReport: {type(e).__name__}"
        claims = body.get("claims") if isinstance(body, dict) else None
        ok = isinstance(claims, list) and all(isinstance(c, dict) and (c.get("claim_text") or c.get("text"))
                                              for c in claims)
        return ok, "structural check only (DiligenceReport schema not available)"
    schema = SCHEMAS.get(kind or "")
    if schema is None:
        return False, f"no output schema registered for kind {kind!r}"
    try:
        schema.model_validate(body)
        return True, None
    except ValidationError as e:
        return False, f"{schema.__name__}: {e.error_count()} validation error(s)"


def timing_ms(trace: list[dict] | None) -> float | None:
    """``ms`` of the last ``{"type": "timing"}`` trace entry; None when the run recorded none."""
    for t in reversed(trace or []):
        if isinstance(t, dict) and t.get("type") == "timing" and isinstance(t.get("ms"), int | float):
            return float(t["ms"])
    return None


def llm_attempted_fallback(report: ResearchReport) -> bool:
    return fallback_info(report.trace, report.kind)[0]


def _counts(report: ResearchReport, res: CheckResult) -> dict[str, Any]:
    registered = {str(s.get("id")).upper() for s in report.sources or [] if isinstance(s, dict) and s.get("id")}
    used = len(res.cited_ids & registered)
    claims_total = len(res.claims)
    return {
        "claims_total": claims_total,
        "fact_claims": sum(1 for c in res.claims if (c.epistemic or "").upper() == "FACT"),
        "citations_total": res.citations_total,
        "invalid_citations": res.invalid_citations,
        "unsupported_claims": len(res.unsupported),
        "unsupported_claim_rate": (len(res.unsupported) / claims_total) if claims_total else None,
        "citation_precision": ((res.citations_total - res.invalid_citations) / res.citations_total
                               if res.citations_total else None),
        "sources_registered": len(registered),
        "sources_used": used,
        "source_coverage": (used / len(registered)) if registered else None,
    }


def evaluate_report(session: Session, report: ResearchReport, *, version: str = EVALUATOR_VERSION
                    ) -> AgentRunEvaluation:
    """Evaluate one report and upsert its row for (report_id, version).

    A HUMAN_REVIEWED row keeps its review status and notes; the automatic metrics are recomputed."""
    agent = agent_for_kind(report.kind)
    if agent is None:
        raise ValueError(f"no agent evaluates reports of kind {report.kind!r}")
    res = check_report(session, report)
    valid, note = structured_valid(report.kind, report.body)
    counts = _counts(report, res)
    notes: list[str] = []
    if note:
        notes.append(note)
    if not res.has_claim_schema:
        notes.append("report kind has no source-cited claims: claim and citation metrics are null")
    elif counts["claims_total"] == 0:
        notes.append("no claims in the report body: claim rates are null")
    latency = timing_ms(report.trace)
    if latency is None:
        notes.append("no timing entry in the trace (generated before timing was recorded): latency is null")
    if report.kind == "memo":
        notes.append("memo does not record an LLM fallback: fallback is not measured")
    if res.unverifiable_citations:
        notes.append(f"{res.unverifiable_citations} cited source(s) had no resolvable content: not token-checked")

    row = session.scalar(select(AgentRunEvaluation).where(AgentRunEvaluation.report_id == report.id,
                                                          AgentRunEvaluation.evaluator_version == version))
    if row is None:
        row = AgentRunEvaluation(report_id=report.id, evaluator_version=version,
                                 review_status=ReviewStatus.AUTOMATIC.value)
        session.add(row)
    complete = report.status == "complete"
    row.agent = agent.name
    row.model = report.model
    row.generator = report.generator
    row.evaluated_at = clock.wall_now()
    row.task_success = complete and valid
    row.structured_output_valid = valid
    for k, v in counts.items():
        setattr(row, k, v)
    if not res.has_claim_schema:
        row.unsupported_claim_rate = row.citation_precision = row.source_coverage = None
    row.latency_ms = latency
    row.cost_usd = float(report.cost_usd or 0.0)
    row.fallback = llm_attempted_fallback(report)
    row.error = not complete
    row.flags = [f.as_dict() for f in res.flags]
    if row.review_status != ReviewStatus.HUMAN_REVIEWED.value:
        row.review_status = ReviewStatus.AUTOMATIC.value
        row.notes = "; ".join(notes)
    session.flush()
    return row


def evaluate_all(session: Session, *, agent: str | None = None, version: str = EVALUATOR_VERSION
                 ) -> list[AgentRunEvaluation]:
    """Evaluate every stored report of an evaluated kind (optionally one agent's, by name)."""
    kinds = [k for a in AGENTS if agent is None or a.name == agent for k in a.kinds]
    if not kinds:
        return []
    reports = session.scalars(select(ResearchReport).where(ResearchReport.kind.in_(kinds))
                              .order_by(ResearchReport.created_at, ResearchReport.id))
    return [evaluate_report(session, r, version=version) for r in reports]


# ── aggregates ───────────────────────────────────────────────────────────────


def _rate(rows: list[AgentRunEvaluation], pred, reason: str) -> dict[str, Any]:
    n = len(rows)
    return metric(sum(1 for r in rows if pred(r)) / n if n else None, n, reason)


def agent_metrics(session: Session, *, version: str = EVALUATOR_VERSION) -> list[dict[str, Any]]:
    """Per runtime agent (registry order): runs, metrics ({value, n, reason}), by_generator, flags, failures."""
    rows_by: dict[str, list[AgentRunEvaluation]] = defaultdict(list)
    for r in session.scalars(select(AgentRunEvaluation).where(AgentRunEvaluation.evaluator_version == version)
                             .order_by(AgentRunEvaluation.evaluated_at, AgentRunEvaluation.id)):
        rows_by[r.agent].append(r)
    open_failures = dict(session.execute(select(AgentFailure.agent, func.count()).where(AgentFailure.status == "OPEN")
                                         .group_by(AgentFailure.agent)).all())
    out = []
    for a in AGENTS:
        rows = rows_by.get(a.name, [])
        n = len(rows)
        none = ("agent writes no report; see LLM telemetry" if a.kind is None
                else "no evaluated runs (run `radar eval`)")
        claims = sum(r.claims_total for r in rows)
        cits = sum(r.citations_total for r in rows)
        with_sources = [r for r in rows if r.sources_registered]
        lat = [r.latency_ms for r in rows if r.latency_ms is not None]
        lat_reason = (none if not n else "no runs with a timing entry (reports generated before timing was recorded)")
        no_claims = (none if not n else "no source-cited claims in these runs"
                     if a.kind != "morning_brief" else "report kind has no source-cited claims")
        pending = sum(1 for r in rows if r.review_status == ReviewStatus.AUTOMATIC.value
                      for f in r.flags or [] if isinstance(f, dict) and f.get("needs_human_review"))
        pending += int(open_failures.get(a.name, 0))
        flags_by_cat: Counter[str] = Counter(str(f.get("category")) for r in rows for f in r.flags or []
                                             if isinstance(f, dict))
        metrics = {
            "success_rate": _rate(rows, lambda r: r.task_success, none),
            "structured_validity_rate": _rate(rows, lambda r: r.structured_output_valid, none),
            "error_rate": _rate(rows, lambda r: r.error, none),
            "fallback_rate": (metric(None, n, "fallback is not recorded for this agent") if a.kind == "memo"
                              else _rate(rows, lambda r: r.fallback, none)),
            "citation_validity": metric((cits - sum(r.invalid_citations for r in rows)) / cits if cits else None,
                                        cits, no_claims if not n else "no citations in these runs"),
            "unsupported_claim_rate": metric(sum(r.unsupported_claims for r in rows) / claims if claims else None,
                                             claims, no_claims),
            "source_coverage": metric(sum(r.source_coverage or 0.0 for r in with_sources) / len(with_sources)
                                      if with_sources else None, len(with_sources),
                                      none if not n else "no runs with registered sources"),
            "avg_sources_used": metric(sum(r.sources_used for r in rows) / n if n else None, n, none),
            "latency_p50_ms": metric(nearest_rank(lat, 50), len(lat), lat_reason),
            "latency_p95_ms": metric(nearest_rank(lat, 95), len(lat), lat_reason),
            "avg_cost_usd": metric(sum(r.cost_usd or 0.0 for r in rows) / n if n else None, n, none),
            "human_review_pending": {"value": pending, "n": n, "reason": None},
        }
        out.append({
            "key": a.key, "name": a.name, "kinds": list(a.kinds), "purposes": list(a.purposes),
            "description": a.description, "runs": n, "metrics": metrics,
            "by_generator": dict(sorted(Counter(r.generator or "unknown" for r in rows).items())),
            "flags_by_category": dict(sorted(flags_by_cat.items())),
            "open_failures": int(open_failures.get(a.name, 0)),
        })
    return out


def llm_telemetry(session: Session) -> list[dict[str, Any]]:
    """Per LLM purpose over the latest calls (see METHODOLOGY['llm_telemetry']), sorted by purpose."""
    rows = session.execute(select(LLMCall.purpose, LLMCall.model, LLMCall.ok, LLMCall.cache_hit, LLMCall.latency_ms,
                                  LLMCall.cost_usd).order_by(LLMCall.created_at.desc()).limit(TELEMETRY_ROWS)).all()
    by: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        by[r.purpose].append(r)
    out = []
    for purpose in sorted(by):
        calls = by[purpose]
        n = len(calls)
        live = [c for c in calls if not c.cache_hit]
        lat = [float(c.latency_ms) for c in live if c.latency_ms]
        ag = agent_for_purpose(purpose)
        out.append({
            "purpose": purpose, "agent": ag.name if ag else "unmapped", "calls": n,
            "live_calls": len(live),
            "ok_rate": metric(sum(1 for c in calls if c.ok) / n, n, "no calls"),
            "latency_p50_ms": metric(nearest_rank(lat, 50), len(lat), "no live calls with a recorded latency"),
            "latency_p95_ms": metric(nearest_rank(lat, 95), len(lat), "no live calls with a recorded latency"),
            "cost_usd": round(sum(float(c.cost_usd or 0.0) for c in calls), 6),
            "cache_hit_rate": metric(sum(1 for c in calls if c.cache_hit) / n, n, "no calls"),
            "models": sorted({c.model for c in calls}),
        })
    return out

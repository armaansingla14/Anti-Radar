"""PORTFOLIO ANALYST regression tasks for the V3 eval runner (pure: no database, no network, no LLM).

* ``portfolio_materiality`` — typed events through ``materiality.classify``: expected level and rule.
* ``portfolio_noise`` — raw changes through ``normalize_one`` + ``classify``: copy edits, docs and routine releases
  must end as NOISE (counted, not surfaced) while real announcements must not.
* ``portfolio_competitor_impact`` — ``impact.impacts_for``: a DIRECT comp's MATERIAL event is MATERIAL for the
  portfolio company, other comp types WATCH, NOISE never propagates.

Registered from ``radar/evals/runner.py`` (bottom import) so YAML cases can name these tasks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from radar.evals.cases import EvalCase
from radar.evals.runner import CaseOutcome, _outcome, register_task
from radar.portfolio import impact, materiality, normalize
from radar.portfolio.types import Candidate, CompanyContext, RawChange, SourceRef

SUBJECT = uuid.UUID("00000000-0000-0000-0000-00000000a001")
T0 = datetime(2026, 11, 1, 12, tzinfo=UTC)
FAMILY = {"website": "web", "usaspending": "commercial", "github": "github", "arxiv": "research",
          "hackernews": "community", "jobs": "hiring"}


def _source(d: dict[str, Any], i: int) -> SourceRef:
    st = str(d.get("source_type") or "website")
    return SourceRef(url=d.get("url") or f"https://example.com/item/{i}", title=str(d.get("title") or ""),
                     source_type=st, family=str(d.get("family") or FAMILY.get(st, st)),
                     publisher=str(d.get("publisher") or "company"))


def _ctx(inp: dict[str, Any]) -> CompanyContext:
    c = inp.get("company") or {}
    return CompanyContext(company_id=SUBJECT, name=str(c.get("name") or "Acme"), domain=c.get("domain"),
                          is_portfolio=True, repo_stars={k.lower(): int(v) for k, v in (c.get("repo_stars") or {})
                                                         .items()},
                          diligence_claims=list(c.get("diligence_claims") or []),
                          tracked_names=list(c.get("tracked_names") or []))


def _candidate(e: dict[str, Any], i: int) -> Candidate:
    sources = [_source(s, i * 10 + j) for j, s in enumerate(e.get("sources") or [{}])]
    return Candidate(company_id=SUBJECT, event_type=str(e["event_type"]), subtype=str(e["subtype"]),
                     title=str(e.get("title") or ""), event_at=T0 - timedelta(days=float(e.get("days_ago") or 0)),
                     sources=sources, epistemic=str(e.get("epistemic") or "FACT"),
                     confidence=float(e.get("confidence", 1.0)), meta=dict(e.get("meta") or {}))


def task_materiality(_s: Any, case: EvalCase, provider: Any) -> CaseOutcome:
    ctx = _ctx(case.input)
    events = [_candidate(e, i) for i, e in enumerate(case.input.get("events") or [])]
    history = events + [_candidate(h, 100 + i) for i, h in enumerate(case.input.get("history") or [])]
    failures: list[str] = []
    for spec, ev in zip(case.input.get("events") or [], events, strict=True):
        level, rule, reason = materiality.classify(ev, ctx, history)
        exp = spec.get("expect") or {}
        if exp.get("materiality") and exp["materiality"] != level:
            failures.append(f"{spec.get('label')}: expected {exp['materiality']}, got {level} ({rule}: {reason})")
        if exp.get("rule") and exp["rule"] != rule:
            failures.append(f"{spec.get('label')}: expected rule {exp['rule']}, got {rule} ({reason})")
    return _outcome(case, provider, failures, {"events": len(events)})


def task_noise(_s: Any, case: EvalCase, provider: Any) -> CaseOutcome:
    ctx = _ctx(case.input)
    failures: list[str] = []
    n = 0
    for i, spec in enumerate(case.input.get("changes") or []):
        src = _source(spec, i)
        raw = RawChange(company_id=SUBJECT, kind=str(spec.get("kind") or "signal"),
                        signal_type=str(spec["signal_type"]), title=str(spec.get("title") or ""),
                        event_at=T0 - timedelta(days=float(spec.get("days_ago") or 0)), source=src,
                        raw_value=spec.get("raw_value"), description=spec.get("description"),
                        meta={"company_name": ctx.name, **dict(spec.get("meta") or {})})
        cand = normalize.normalize_one(raw)
        if cand is None:
            failures.append(f"{spec.get('label')}: not normalised")
            continue
        n += 1
        level, rule, reason = materiality.classify(cand, ctx, [cand])
        exp = spec.get("expect") or {}
        if exp.get("materiality") and exp["materiality"] != level:
            failures.append(f"{spec.get('label')}: expected {exp['materiality']}, got {level} "
                            f"({cand.event_type}.{cand.subtype}, {rule}: {reason})")
        if exp.get("subtype") and exp["subtype"] != cand.subtype:
            failures.append(f"{spec.get('label')}: expected subtype {exp['subtype']}, got {cand.subtype}")
        if exp.get("rule") and exp["rule"] != rule:
            failures.append(f"{spec.get('label')}: expected rule {exp['rule']}, got {rule} ({reason})")
        if exp.get("event_type") and exp["event_type"] != cand.event_type:
            failures.append(f"{spec.get('label')}: expected type {exp['event_type']}, got {cand.event_type}")
        if exp.get("not_materiality") and exp["not_materiality"] == level:
            failures.append(f"{spec.get('label')}: must not be {level} ({cand.event_type}.{cand.subtype})")
    return _outcome(case, provider, failures, {"changes": n})


def task_competitor_impact(_s: Any, case: EvalCase, provider: Any) -> CaseOutcome:
    failures: list[str] = []
    for spec in case.input.get("checks") or []:
        pid = uuid.uuid4()
        link = impact.CompLink(pid, "Portfolio Co", SUBJECT, "Comp Co", str(spec["comp_type"]),
                               "builds the same product for the same buyers", str(spec.get("confidence") or "HIGH"))
        subject_in_portfolio = bool(spec.get("subject_is_portfolio"))
        out = impact.impacts_for(subject_id=SUBJECT, subject_name="Comp Co", materiality=str(spec["materiality"]),
                                 confidence=1.0, portfolio_ids={pid} | ({SUBJECT} if subject_in_portfolio else set()),
                                 comps_of_subject=[link])
        got = next((i for i in out if i.company_id == pid), None)
        exp = spec.get("expect")
        label = spec.get("label")
        if exp is None:
            if got is not None:
                failures.append(f"{label}: expected no impact, got {got.materiality}")
        elif got is None:
            failures.append(f"{label}: expected {exp}, got no impact")
        else:
            if got.materiality != exp.get("materiality"):
                failures.append(f"{label}: expected {exp.get('materiality')}, got {got.materiality}")
            if exp.get("relation") and got.relation != exp["relation"]:
                failures.append(f"{label}: expected relation {exp['relation']}, got {got.relation}")
            rc = exp.get("reason_contains")
            if rc and rc not in got.reason:
                failures.append(f"{label}: reason {got.reason!r} lacks {rc!r}")
        if subject_in_portfolio and not any(i.relation == "SELF" for i in out) and spec["materiality"] != "NOISE":
            failures.append(f"{label}: portfolio subject has no SELF impact")
    return _outcome(case, provider, failures, {"checks": len(case.input.get("checks") or [])})


def task_newsroom(_s: Any, case: EvalCase, provider: Any) -> CaseOutcome:
    """Dated items extracted from newsroom HTML (inline in the case): exactly the expected (url, title, date)."""
    from radar.providers.newsroom import extract_items, post_page_item

    now = datetime(2026, 11, 6, 18, tzinfo=UTC)
    failures: list[str] = []
    n = 0
    for spec in case.input.get("pages") or []:
        label = spec.get("label")
        if spec.get("post_page"):
            item = post_page_item(str(spec["html"]), str(spec["url"]), now=now)
            got = {(item.url, item.title, item.published_at.date().isoformat())} if item else set()
        else:
            got = {(i.url, i.title, i.published_at.date().isoformat())
                   for i in extract_items(str(spec["html"]), str(spec["url"]), now=now)}
        want = {(e["url"], e["title"], str(e["date"])) for e in spec.get("expect") or []}
        n += len(got)
        if got != want:
            failures.append(f"{label}: missing {sorted(want - got)}; unexpected {sorted(got - want)}")
    return _outcome(case, provider, failures, {"items": n})


register_task("portfolio_materiality", task_materiality)
register_task("portfolio_newsroom", task_newsroom)
register_task("portfolio_noise", task_noise)
register_task("portfolio_competitor_impact", task_competitor_impact)

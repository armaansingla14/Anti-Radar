"""Run regression cases (``evals/cases.py``) and store their outcomes (``eval_case_results``).

A task is a function ``(session | None, case, provider | None) -> CaseOutcome``. Tasks that build database
fixtures run inside ``session.begin_nested()`` and are always rolled back, so a case never leaves rows behind
(LLM call logs, written through their own session, are the exception by design).
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar import clock
from radar.evals.cases import EvalCase
from radar.evals.checks import (
    AFFILIATION_RE,
    CheckResult,
    ClaimRef,
    Flag,
    SourceInfo,
    check_claims,
    check_report,
    iter_claims,
    report_time,
)
from radar.evals.metrics import structured_valid
from radar.llm.guard import detect_injection
from radar.llm.provider import BaseProvider
from radar.models import EvalCaseResult

TaskFn = Callable[[Session | None, EvalCase, BaseProvider | None], "CaseOutcome"]


@dataclass
class CaseOutcome:
    case_id: str
    agent: str
    task: str
    provider: str
    model: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    latency_ms: float | None = None
    cost_usd: float = 0.0
    error: str | None = None


@dataclass(frozen=True)
class TaskSpec:
    fn: TaskFn
    needs_db: bool = False
    llm_capable: bool = False


TASKS: dict[str, TaskSpec] = {}


def register_task(name: str, fn: TaskFn, *, needs_db: bool = False, llm_capable: bool = False) -> None:
    TASKS[name] = TaskSpec(fn=fn, needs_db=needs_db, llm_capable=llm_capable)


def provider_label(provider: BaseProvider | None) -> tuple[str, str]:
    if provider is None or not provider.available:
        return "template", "template"
    try:
        model = provider.model_for("deep")
    except Exception:  # noqa: BLE001
        model = "unknown"
    return provider.name, model


def _metrics(*, completed: bool = True, citation_validity: float | None = None, unsupported: int | None = None,
             coverage: float | None = None, structured: bool | None = None, **extra: Any) -> dict[str, Any]:
    return {"task_completed": completed, "citation_validity": citation_validity, "unsupported_claims": unsupported,
            "coverage": coverage, "structured_valid": structured, **extra}


def _outcome(case: EvalCase, provider: BaseProvider | None, failures: list[str], metrics: dict[str, Any],
             cost: float = 0.0) -> CaseOutcome:
    prov, model = provider_label(provider)
    return CaseOutcome(case_id=case.id, agent=case.agent, task=case.task, provider=prov, model=model,
                       passed=not failures, failures=failures, metrics=metrics, cost_usd=cost)


def run_case(session: Session | None, case: EvalCase, *, provider: BaseProvider | None = None) -> CaseOutcome:
    """Run one case. Never raises: an exception becomes a failed outcome with ``error``."""
    spec = TASKS.get(case.task)
    prov, model = provider_label(provider)
    if spec is None:
        return CaseOutcome(case.id, case.agent, case.task, prov, model, False, [f"unknown task {case.task}"],
                           error=f"unknown task {case.task}")
    if spec.needs_db and session is None:
        return CaseOutcome(case.id, case.agent, case.task, prov, model, False, ["task needs a database"],
                           error="task needs a database")
    nested = session.begin_nested() if (spec.needs_db and session is not None) else None
    t0 = time.perf_counter()
    try:
        out = spec.fn(session if spec.needs_db else None, case, provider)
    except Exception as e:  # noqa: BLE001 — a broken case is a failed case, not a crashed run
        out = CaseOutcome(case.id, case.agent, case.task, prov, model, False, [f"{type(e).__name__}: {e}"],
                          metrics=_metrics(completed=False), error=f"{type(e).__name__}: {e}"[:2000])
    finally:
        if nested is not None and nested.is_active:
            nested.rollback()
    out.latency_ms = round((time.perf_counter() - t0) * 1000, 1)
    return out


def run_cases(session: Session | None, cases: Iterable[EvalCase], *, provider: BaseProvider | None = None
              ) -> list[CaseOutcome]:
    return [run_case(session, c, provider=provider) for c in cases]


def store_outcomes(session: Session, outcomes: Iterable[CaseOutcome], *, batch_id: uuid.UUID | None = None
                   ) -> uuid.UUID:
    batch = batch_id or uuid.uuid4()
    now = clock.wall_now()
    for o in outcomes:
        session.add(EvalCaseResult(
            batch_id=batch, case_id=o.case_id[:80], agent=o.agent[:40], task=o.task[:40], provider=o.provider[:20],
            model=o.model[:80], run_at=now, passed=o.passed, metrics={**o.metrics, "failures": o.failures},
            latency_ms=o.latency_ms, cost_usd=o.cost_usd, error=o.error,
        ))
    session.flush()
    return batch


def latest_results(session: Session, *, provider: str | None = "template") -> dict[str, EvalCaseResult]:
    """Newest result per case (by default from the template baseline, i.e. `radar eval` runs)."""
    q = select(EvalCaseResult)
    if provider:
        q = q.where(EvalCaseResult.provider == provider)
    out: dict[str, EvalCaseResult] = {}
    for r in session.scalars(q.order_by(EvalCaseResult.run_at.desc(), EvalCaseResult.id)):
        out.setdefault(r.case_id, r)
    return out


# ── assertion helpers ────────────────────────────────────────────────────────


def _flag_matches(f: Flag, m: dict[str, Any] | str) -> bool:
    if isinstance(m, str):
        return m in (f.rule, str(f.category))
    for k in ("rule", "category", "severity", "section"):
        if k in m and str(getattr(f, k)) != str(m[k]):
            return False
    if "needs_human_review" in m and bool(f.needs_human_review) != bool(m["needs_human_review"]):
        return False
    if "claim_contains" in m and str(m["claim_contains"]).lower() not in (f.claim_text or "").lower():
        return False
    return True


def assert_flags(flags: list[Flag], expected: list[Any] | None, forbidden: list[Any] | None, label: str = ""
                 ) -> list[str]:
    out = []
    for m in expected or []:
        if not any(_flag_matches(f, m) for f in flags):
            out.append(f"{label}expected flag {m} not raised (got: {sorted({f.rule for f in flags})})")
    for m in forbidden or []:
        hit = [f for f in flags if _flag_matches(f, m)]
        if hit:
            out.append(f"{label}unexpected flag {m}: {hit[0].rule} on {hit[0].claim_text!r}")
    return out


def _sources_from_input(items: list[dict[str, Any]], report_at) -> dict[str, SourceInfo]:
    out = {}
    for s in items or []:
        days = s.get("fetched_days_before_report")
        out[str(s["id"]).upper()] = SourceInfo(
            id=str(s["id"]).upper(), url=s.get("url"), title=s.get("title"), source_type=s.get("source_type"),
            fetched_at=(report_at - timedelta(days=float(days))) if days is not None else None,
            content=s.get("snippet") if "snippet" in s else None)
    return out


def _claims_from_input(items: list[dict[str, Any]]) -> list[ClaimRef]:
    return [ClaimRef(section=str(c.get("section") or "claims"), text=str(c["text"]),
                     epistemic=c.get("epistemic", "FACT"), source_ids=[str(x) for x in c.get("source_ids") or []])
            for c in items or []]


def _counts_check(res: CheckResult, expected: dict[str, Any]) -> list[str]:
    out = []
    for key, actual in (("unsupported", len(res.unsupported)), ("possibly_unrelated", res.possibly_unrelated),
                        ("unverifiable_citations", res.unverifiable_citations),
                        ("invalid_citations", res.invalid_citations)):
        if key in expected and int(expected[key]) != actual:
            out.append(f"expected {key} = {expected[key]}, got {actual}")
    return out


def _claim_metrics(res: CheckResult, registered: int | None = None, structured: bool | None = None
                   ) -> dict[str, Any]:
    validity = ((res.citations_total - res.invalid_citations) / res.citations_total
                if res.citations_total else None)
    coverage = (len(res.cited_ids) / registered) if registered else None
    return _metrics(citation_validity=validity, unsupported=len(res.unsupported), coverage=coverage,
                    structured=structured, claims=len(res.claims))


# ── pure tasks ───────────────────────────────────────────────────────────────


def task_claim_checks(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    report_at = clock.now()
    inp, exp = case.input, case.expected
    sources = _sources_from_input(inp.get("sources") or [], report_at)
    res = check_claims(_claims_from_input(inp.get("claims") or []), sources, report_at=report_at,
                       subject_domain=inp.get("subject_domain"))
    failures = assert_flags(res.flags, exp.get("flags"), exp.get("no_flags")) + _counts_check(res, exp)
    return _outcome(case, provider, failures, _claim_metrics(res, len(sources)))


def task_citation_validation(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.agent.brief_schema import Claim
    from radar.llm.guard import validate_citations

    report_at = clock.now()
    inp, exp = case.input, case.expected
    valid_ids = [str(v).upper() for v in inp.get("valid_ids") or []]
    sources = _sources_from_input(inp.get("sources") or [{"id": v} for v in valid_ids], report_at)
    before = _claims_from_input(inp.get("claims") or [])
    pre = check_claims(before, sources, report_at=report_at)
    failures = assert_flags(pre.flags, exp.get("pre_flags"), exp.get("pre_no_flags"), "before validation: ")
    claims = [Claim(text=c.text, epistemic=c.epistemic, source_ids=c.source_ids) for c in before]
    rep = validate_citations(claims, valid_ids)
    if "invalid_ids" in exp and sorted(rep.invalid_ids) != sorted(str(x).upper() for x in exp["invalid_ids"]):
        failures.append(f"expected invalid_ids {exp['invalid_ids']}, got {rep.invalid_ids}")
    if "downgraded" in exp and rep.downgraded != int(exp["downgraded"]):
        failures.append(f"expected {exp['downgraded']} downgraded, got {rep.downgraded}")
    after = [ClaimRef(section=b.section, text=c.text, epistemic=c.epistemic, source_ids=list(c.source_ids))
             for b, c in zip(before, claims, strict=True)]
    failures += _epistemic_check(after, case.expected_epistemic)
    for sub in exp.get("after_contains") or []:
        if not any(sub in c.text for c in after):
            failures.append(f"no claim text contains {sub!r} after validation")
    post = check_claims(after, sources, report_at=report_at)
    failures += assert_flags(post.flags, exp.get("post_flags"), exp.get("post_no_flags"), "after validation: ")
    m = _claim_metrics(post, len(sources))
    m["guard_invalid_ids"] = rep.invalid_ids
    m["guard_downgraded"] = rep.downgraded
    return _outcome(case, provider, failures, m)


def _epistemic_check(claims: list[ClaimRef], expected: dict[str, str]) -> list[str]:
    out = []
    for needle, label in (expected or {}).items():
        hits = [c for c in claims if needle.lower() in c.text.lower()]
        if not hits:
            out.append(f"no claim contains {needle!r}")
        elif any((c.epistemic or "") != label for c in hits):
            out.append(f"claim containing {needle!r} labelled {[c.epistemic for c in hits]}, expected {label}")
    return out


def _checks_list(case: EvalCase, fn: Callable[[dict[str, Any]], tuple[Any, Any]]) -> list[str]:
    failures = []
    for i, chk in enumerate(case.input.get("checks") or []):
        got, want = fn(chk)
        if got != want:
            failures.append(f"check {i + 1} ({chk.get('label') or chk}): expected {want!r}, got {got!r}")
    if not case.input.get("checks"):
        failures.append("case has no checks")
    return failures


def task_formation_public(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.pipeline.aliases import org_key
    from radar.scoring.formation import public_founder_org

    today = clock.today()

    def one(chk: dict[str, Any]) -> tuple[Any, Any]:
        created = {org_key(k): today - timedelta(days=int(v)) for k, v in (chk.get("orgs") or {}).items()}
        p = SimpleNamespace(bio=chk.get("bio"), current_company=chk.get("current_company"), attrs={})
        got = public_founder_org(p, created, today)
        return (got[0] if got else None), chk.get("expect")

    failures = _checks_list(case, one)
    return _outcome(case, provider, failures, _metrics(checks=len(case.input.get("checks") or [])))


def task_current_org_elsewhere(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.pipeline.aliases import org_key
    from radar.scoring.formation import current_org_elsewhere

    today = clock.today()

    def one(chk: dict[str, Any]) -> tuple[Any, Any]:
        orgs = chk.get("github_orgs") or []
        created = {org_key(o["login"]): today - timedelta(days=int(o["days_old"])) for o in orgs
                   if o.get("days_old") is not None}
        p = SimpleNamespace(bio=None, current_company=chk.get("company"),
                            attrs={"github_orgs": [{k: v for k, v in o.items() if k != "days_old"} for o in orgs]})
        got = current_org_elsewhere(p, created, today)
        return (got[0] if got else None), chk.get("expect")

    failures = _checks_list(case, one)
    return _outcome(case, provider, failures, _metrics(checks=len(case.input.get("checks") or [])))


def task_new_project_noise(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.scoring.extract import assess_new_project

    def one(chk: dict[str, Any]) -> tuple[Any, Any]:
        q = assess_new_project(chk["repo"], stars=int(chk.get("stars", 0)), forks=int(chk.get("forks", 0)),
                               contributors=int(chk.get("contributors", 0)), serial=int(chk.get("serial", 1)),
                               thesis_matches=chk.get("thesis_matches") or ())
        want = chk.get("expect") or {}
        got = {k: (q.counts if k == "counts" else q.notable) for k in want}
        return got, want

    failures = _checks_list(case, one)
    return _outcome(case, provider, failures, _metrics(checks=len(case.input.get("checks") or [])))


def task_bot_filter(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.providers.github import is_bot

    failures = _checks_list(case, lambda chk: (is_bot(chk["login"]), bool(chk["expect"])))
    return _outcome(case, provider, failures, _metrics(checks=len(case.input.get("checks") or [])))


def task_why_anti_copy(_s: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.services import presenters

    inp, exp = case.input, case.expected
    snap = SimpleNamespace(components={"founder_signal": dict(inp.get("founder_signal") or {})}, attention={},
                           collision={})
    lines = presenters.why_anti(SimpleNamespace(stage=inp.get("stage")), [], snap)
    failures = []
    if not inp.get("founder_relation", False):
        bad = [ln for ln in lines if re.search(exp.get("forbidden_pattern") or r"founder|founding", ln, re.IGNORECASE)]
        if bad:
            failures.append(f"founder wording without a founder relation: {bad}")
    for want in exp.get("must_include") or []:
        if want not in lines:
            failures.append(f"expected line {want!r}, got {lines}")
    return _outcome(case, provider, failures, _metrics(lines=lines))


# ── database tasks (fixtures built in a rolled-back savepoint) ───────────────


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


def _add_signals(session: Session, entity_id: uuid.UUID, entity_type: str, items: list[dict[str, Any]]) -> None:
    from radar.models import Signal

    now = clock.now()
    for s in items or []:
        session.add(Signal(
            entity_id=entity_id, entity_type=entity_type, signal_type=s.get("signal_type") or "mention",
            source_type=s.get("source_type") or "github", source_url=s.get("url"), title=s["title"],
            description=s.get("description"), normalized_value=s.get("normalized_value", 0.5),
            event_at=now - timedelta(days=float(s.get("days_ago", 1))), dedup_key=uuid.uuid4().hex,
            epistemic=s.get("epistemic", "FACT")))


def _add_repos(session: Session, items: list[dict[str, Any]], *, company_id: uuid.UUID | None = None,
               person_id: uuid.UUID | None = None) -> None:
    from radar.models import RepoContributor, Repository

    now = clock.now()
    for r in items or []:
        repo = Repository(full_name=r["full_name"], owner=r["full_name"].split("/", 1)[0], company_id=company_id,
                          description=r.get("description"), stars=int(r.get("stars", 0)), forks=int(r.get("forks", 0)),
                          language=r.get("language"), readme_excerpt=r.get("readme_excerpt"),
                          gh_created_at=now - timedelta(days=float(r.get("days_ago", 30))),
                          pushed_at=now - timedelta(days=float(r.get("pushed_days_ago", 2))))
        session.add(repo)
        session.flush()
        if person_id is not None:
            session.add(RepoContributor(repository_id=repo.id, person_id=person_id,
                                        contributions=int(r.get("contributions", 20))))


def _add_papers(session: Session, person_id: uuid.UUID, items: list[dict[str, Any]]) -> None:
    from radar.models import PaperAuthor, ResearchPaper

    now = clock.now()
    for p in items or []:
        paper = ResearchPaper(arxiv_id=p["arxiv_id"], title=p["title"], abstract=p.get("abstract"),
                              url=p.get("url") or f"https://arxiv.org/abs/{p['arxiv_id']}",
                              published_at=now - timedelta(days=float(p.get("days_ago", 60))),
                              authors=[{"name": n} for n in p.get("authors") or []])
        session.add(paper)
        session.flush()
        session.add(PaperAuthor(paper_id=paper.id, person_id=person_id))


def _person(session: Session, d: dict[str, Any]):
    from radar.models import Person

    p = Person(slug=f"eval-case-{_suffix()}", name=d["name"], bio=d.get("bio"),
               current_company=d.get("current_company"), current_role=d.get("current_role"),
               prior_affiliations=list(d.get("prior_affiliations") or []), github_handle=d.get("github_handle"),
               attrs=dict(d.get("attrs") or {}))
    session.add(p)
    session.flush()
    return p


def _report_checks(session: Session, case: EvalCase, report) -> tuple[list[str], CheckResult, bool]:
    """Assertions shared by the report-producing tasks."""
    exp = case.expected
    res = check_report(session, report)
    valid, note = structured_valid(report.kind, report.body)
    failures: list[str] = []
    if exp.get("structured_valid", True) and not valid:
        failures.append(f"structured output invalid: {note}")
    cited_claims = sum(1 for c in res.claims if c.source_ids)
    if case.min_citations is not None and cited_claims < case.min_citations:
        failures.append(f"expected >= {case.min_citations} cited claims, got {cited_claims}")
    failures += _epistemic_check(res.claims, case.expected_epistemic)
    for org in exp.get("no_employment_fact_about") or []:
        bad = [c.text for c in res.claims if (c.epistemic or "") == "FACT" and AFFILIATION_RE.search(c.text)
               and org.lower() in c.text.lower()]
        if bad:
            failures.append(f"employment FACT about {org}: {bad[0]!r}")
    for cat, n in (exp.get("max_flags") or {}).items():
        got = sum(1 for f in res.flags if str(f.category) == cat or f.rule == cat)
        if got > int(n):
            failures.append(f"expected at most {n} {cat} flag(s), got {got}")
    if exp.get("no_high_flags"):
        high = [f for f in res.flags if str(f.severity) == "HIGH"]
        if high:
            failures.append(f"HIGH flag {high[0].rule}: {high[0].claim_text or high[0].detail!r}")
    if exp.get("all_facts_cited"):
        bad = [c.text for c in res.claims if (c.epistemic or "") == "FACT" and not c.source_ids]
        if bad:
            failures.append(f"uncited FACT: {bad[0]!r}")
    if exp.get("no_injection_in_output") and any(f.rule == "output_follows_injection" for f in res.flags):
        failures.append("output contains injection patterns")
    failures += assert_flags(res.flags, exp.get("flags"), exp.get("no_flags"))
    by_url = {(s.url or ""): sid for sid, s in res.sources.items()}
    for i, probe in enumerate(exp.get("probe_claims") or []):
        ids = []
        for u in probe.get("cite_urls") or []:
            sid = by_url.get(u)
            if sid is None:
                failures.append(f"probe {i + 1}: no registered source with url {u}")
            else:
                ids.append(sid)
        claim = ClaimRef(section=f"probe_{i + 1}", text=probe["text"], epistemic=probe.get("epistemic", "FACT"),
                         source_ids=ids)
        pr = check_claims([claim], res.sources, report_at=report_time(report))
        failures += assert_flags(pr.flags, probe.get("expect_flags"), probe.get("expect_no_flags"),
                                 f"probe {i + 1}: ")
    return failures, res, valid


def task_meeting_brief_template(session: Session | None, case: EvalCase, provider: BaseProvider | None
                                ) -> CaseOutcome:
    from radar.agent.meeting import generate_meeting_brief
    from radar.llm.provider import NullProvider
    from radar.models import Company, CompanyPerson

    assert session is not None
    inp = case.input
    person = _person(session, inp["person"])
    _add_signals(session, person.id, "person", inp.get("signals") or [])
    _add_repos(session, inp.get("repos") or [], person_id=person.id)
    _add_papers(session, person.id, inp.get("papers") or [])
    for o in inp.get("orgs") or []:
        c = Company(slug=f"eval-org-{_suffix()}", name=o["name"], kind=o.get("kind", "company"))
        session.add(c)
        session.flush()
        session.add(CompanyPerson(company_id=c.id, person_id=person.id, role=o.get("role"),
                                  is_founder=bool(o.get("is_founder")), source_url=o.get("source_url")))
    session.flush()
    report = generate_meeting_brief(session, person.id, force=True, provider=provider or NullProvider())
    failures, res, valid = _report_checks(session, case, report)
    return _outcome(case, provider, failures, _claim_metrics(res, len(report.sources or []), valid),
                    cost=float(report.cost_usd or 0.0))


def _company_fixture(session: Session, inp: dict[str, Any]):
    from radar.models import Company, CompanyPerson

    c_in = inp["company"]
    c = Company(slug=f"eval-co-{_suffix()}", name=c_in["name"], description=c_in.get("description"),
                website=c_in.get("website"), kind=c_in.get("kind", "company"), stage=c_in.get("stage", "unknown"),
                github_org=c_in.get("github_org"), thesis_tags=list(c_in.get("thesis_tags") or []))
    session.add(c)
    session.flush()
    _add_repos(session, inp.get("repos") or [], company_id=c.id)
    _add_signals(session, c.id, "company", inp.get("signals") or [])
    for p_in in inp.get("people") or []:
        p = _person(session, p_in)
        session.add(CompanyPerson(company_id=c.id, person_id=p.id, role=p_in.get("role"),
                                  is_founder=bool(p_in.get("is_founder")), source_url=p_in.get("source_url")))
    session.flush()
    return c


def task_anti_brief_template(session: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.agent.analyst import investigate
    from radar.llm.provider import NullProvider

    assert session is not None
    c = _company_fixture(session, case.input)
    report = investigate(session, "company", c.id, live_fetch=False, provider=provider or NullProvider())
    failures, res, valid = _report_checks(session, case, report)
    return _outcome(case, provider, failures, _claim_metrics(res, len(report.sources or []), valid),
                    cost=float(report.cost_usd or 0.0))


def task_injection(session: Session | None, case: EvalCase, provider: BaseProvider | None) -> CaseOutcome:
    from radar.agent.analyst import investigate
    from radar.llm.provider import NullProvider

    assert session is not None
    inp, exp = case.input, case.expected
    inp = {**inp, "repos": [{**r, "readme_excerpt": inp["injection"]} if i == 0 else r
                            for i, r in enumerate(inp.get("repos") or [])]}
    c = _company_fixture(session, inp)
    report = investigate(session, "company", c.id, live_fetch=False, provider=provider or NullProvider())
    failures, res, valid = _report_checks(session, case, report)
    repo_url = f"https://github.com/{inp['repos'][0]['full_name']}"
    flagged = [f for t in report.trace or [] if isinstance(t, dict) for f in t.get("flags") or []
               if isinstance(f, dict)]
    if not any(repo_url in str(f.get("url")) for f in flagged):
        failures.append(f"trace has no injection flag naming {repo_url}")
    out_texts = [c.text for c in iter_claims(report.body)]
    for k in ("questions_to_ask_founders", "unknowns"):
        out_texts += [str(x) for x in (report.body or {}).get(k) or []]
    blob = "\n".join(out_texts + [report.markdown or ""]).lower()
    for phrase in exp.get("forbidden_output") or []:
        if phrase.lower() in blob:
            failures.append(f"output contains injected text {phrase!r}")
    hits = [h for t in out_texts for h in detect_injection(t)]
    if hits:
        failures.append(f"injection patterns in output: {hits[:3]}")
    inj = [f for f in res.flags if str(f.category) == "PROMPT_INJECTION_ATTEMPT"]
    if not inj:
        failures.append("no PROMPT_INJECTION_ATTEMPT flag")
    if any(str(f.severity) != "INFO" for f in inj):
        failures.append("PROMPT_INJECTION_ATTEMPT flagged above INFO although the output ignored it")
    return _outcome(case, provider, failures, _claim_metrics(res, len(report.sources or []), valid),
                    cost=float(report.cost_usd or 0.0))


register_task("claim_checks", task_claim_checks)
register_task("citation_validation", task_citation_validation)
register_task("formation_public", task_formation_public)
register_task("current_org_elsewhere", task_current_org_elsewhere)
register_task("new_project_noise", task_new_project_noise)
register_task("bot_filter", task_bot_filter)
register_task("why_anti_copy", task_why_anti_copy)
register_task("meeting_brief_template", task_meeting_brief_template, needs_db=True, llm_capable=True)
register_task("anti_brief_template", task_anti_brief_template, needs_db=True, llm_capable=True)
register_task("injection", task_injection, needs_db=True, llm_capable=True)

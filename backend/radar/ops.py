"""High-level operations shared by the CLI, the worker and the API.

Every ingestion goes through `pipeline.runner.run_provider`, so each run is
recorded in `ingestion_runs` (records, rejects, errors, latency, rate limits)
and failures never disappear silently.

When a `SnapshotWriter` is active (``radar demo capture``) each provider batch is
also written to the demo snapshot so the same real data can be replayed offline.
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

from radar import clock
from radar.db import session_scope
from radar.pipeline.runner import run_provider
from radar.providers.base import ProviderBatch
from radar.thesis.loader import get_thesis

log = logging.getLogger("radar.ops")


# ── Snapshot capture ─────────────────────────────────────────────────────────


class SnapshotWriter:
    def __init__(self, directory: Path) -> None:
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.n = len(list(self.dir.glob("*.json.gz")))

    def write(self, provider: str, params: dict[str, Any], batch: ProviderBatch, run: Any) -> None:
        self.n += 1
        path = self.dir / f"{self.n:04d}_{provider}.json.gz"
        run_meta = {
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "request_count": run.request_count,
            "latency_ms_p50": run.latency_ms_p50,
            "rate_limit_remaining": run.rate_limit_remaining,
            "errors": run.errors or [],
        }
        payload = {"provider": provider, "params": params, "run": run_meta, "batch": batch.model_dump(mode="json")}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(payload, f, separators=(",", ":"))


_writer: SnapshotWriter | None = None


@contextmanager
def capturing(directory: Path):
    global _writer
    _writer = SnapshotWriter(directory)
    try:
        yield _writer
    finally:
        _writer = None


def _run(provider: str, fn: Callable[[], ProviderBatch], params: dict[str, Any], stats=None):
    holder: dict[str, ProviderBatch] = {}

    def wrapped() -> ProviderBatch:
        holder["batch"] = fn()
        return holder["batch"]

    run, stats_out = run_provider(session_scope, provider, wrapped, params=params, http_stats=stats)
    if _writer is not None and "batch" in holder:
        _writer.write(provider, params, holder["batch"], run)
    return run, stats_out


# ── Ingestion ────────────────────────────────────────────────────────────────


def ingest_github(
    *,
    queries: list[str] | None = None,
    repo: str | None = None,
    org: str | None = None,
    per_query: int = 4,
    max_repos: int = 60,
    created_within_days: int = 150,
):
    from radar.providers.github import GitHubProvider

    gh = GitHubProvider()
    if repo:
        params = {"repo": repo}
        return _run("github", lambda: gh.fetch_repo(repo, deep=True), params, gh.client.stats)
    if org:
        params = {"org": org}
        return _run("github", lambda: gh.fetch_org(org), params, gh.client.stats)
    qs = queries or get_thesis().discovery_queries
    params = {"queries": qs, "per_query": per_query, "max_repos": max_repos}
    return _run(
        "github",
        lambda: gh.discover(qs, per_query=per_query, max_repos=max_repos, created_within_days=created_within_days),
        params,
        gh.client.stats,
    )


def ingest_arxiv(*, categories: list[str] | None = None, max_results: int | None = None, days: int = 21,
                 query: str | None = None):
    from radar.providers.arxiv import ArxivProvider

    ax = ArxivProvider()
    if query:
        return _run("arxiv", lambda: ax.search(query, max_results=max_results or 25, days=days),
                    {"query": query}, ax.client.stats)
    params = {"categories": categories, "max_results": max_results, "days": days}
    return _run("arxiv", lambda: ax.fetch_recent(categories, max_results=max_results, days=days), params,
                ax.client.stats)


def ingest_hn(*, days: int = 14, topics: bool = True, topic_days: int = 45):
    from radar.providers.hackernews import HackerNewsProvider

    hn = HackerNewsProvider()

    def fn() -> ProviderBatch:
        batch = hn.fetch_launches(days=days)
        if topics:
            batch.extend(hn.search_topics(get_thesis().discovery_queries, days=topic_days))
        return batch

    return _run("hackernews", fn, {"days": days, "topics": topics}, hn.client.stats)


def ingest_url(url: str, company_name: str | None = None, max_pages: int = 6):
    from radar.providers.website import WebsiteProvider

    web = WebsiteProvider()
    return _run("website", lambda: web.crawl(url, company_name=company_name, max_pages=max_pages),
                {"url": url}, web.client.stats)


def ingest_x_manual(url: str, pasted_text: str | None = None, entity_ref=None):
    from radar.providers.x.manual import ManualXSignalProvider

    x = ManualXSignalProvider()
    info: dict[str, Any] = {}

    def fn() -> ProviderBatch:
        batch, i = x.ingest(url, pasted_text, entity_ref=entity_ref)
        info.update(i or {})
        return batch

    run, stats = _run("x", fn, {"url": url, "manual": True}, x.client.stats)
    return run, stats, info


def crawl_company_sites(limit: int = 25, *, only_missing: bool = True):
    """Crawl websites of the highest-scoring companies with a known domain."""
    from sqlalchemy import select

    from radar.models import Company, ScoreSnapshot, WebPage

    with session_scope() as s:
        q = (
            select(Company.website, Company.name, Company.domain)
            .join(ScoreSnapshot, ScoreSnapshot.entity_id == Company.id, isouter=True)
            .where(Company.domain.is_not(None), Company.is_demo.is_(False))
            .order_by(ScoreSnapshot.score.desc().nulls_last())
        )
        crawled = {u for (u,) in s.execute(select(WebPage.url))} if only_missing else set()
        targets = []
        for website, name, domain in s.execute(q):
            url = website or f"https://{domain}"
            if any(domain in c for c in crawled):
                continue
            if url not in {t[0] for t in targets}:
                targets.append((url, name))
            if len(targets) >= limit:
                break
    results = []
    for url, name in targets:
        try:
            results.append(ingest_url(url, company_name=name))
        except Exception as e:  # noqa: BLE001 — recorded by run_provider; keep sweeping
            log.warning("crawl failed for %s: %s", url, e)
    return results


def probe_attention(limit: int = 60):
    """Measure Hacker News attention for the highest-momentum companies.

    Absence of a story in our corpus is not evidence of zero attention, so before RADAR
    claims "no HN discussion" (Attention Gap, quiet-velocity strange signal) it asks HN
    directly: Algolia search over 90 days for the company's flagship repo and domain.
    Stored as metric_snapshots `hn_probe_mentions` / `hn_probe_points` (FACT, dated)."""
    from sqlalchemy import select

    from radar.models import Company, Repository, ScoreSnapshot
    from radar.providers.base import MetricPoint, ProviderBatch, domain_ref, gh_org_ref
    from radar.providers.hackernews import HackerNewsProvider

    day = clock.today()
    with session_scope() as s:
        rows = s.execute(
            select(Company, ScoreSnapshot)
            .join(ScoreSnapshot, ScoreSnapshot.entity_id == Company.id)
            .where(Company.is_demo.is_(False), ScoreSnapshot.day == day)
        ).all()
        rows.sort(key=lambda cs: -float(((cs[1].components or {}).get("technical_momentum") or {}).get("value", 0)))
        targets = []
        for c, _snap in rows[:limit]:
            repo = s.scalar(select(Repository).where(Repository.company_id == c.id)
                            .order_by(Repository.stars.desc()).limit(1))
            terms = [t for t in (repo.full_name if repo else None, c.domain) if t]
            ref = gh_org_ref(c.github_org) if c.github_org else (domain_ref(c.domain) if c.domain else None)
            if terms and ref is not None:
                targets.append((ref, terms))
    hn = HackerNewsProvider()

    def fn() -> ProviderBatch:
        batch = ProviderBatch(provider="hackernews")
        for ref, terms in targets:
            mentions, points, failed = 0, 0, False
            for term in terms:
                m = hn.mentions(term, days=90)
                if m.get("error"):
                    failed = True
                    batch.errors.append(f"probe {term}: {m['error']}")
                    break
                mentions += m.get("count", 0)
                points += m.get("points_total", 0)
            if failed:
                continue
            batch.metrics.append(MetricPoint(entity=ref, metric="hn_probe_mentions", day=day, value=mentions))
            batch.metrics.append(MetricPoint(entity=ref, metric="hn_probe_points", day=day, value=points))
        return batch

    return _run("hackernews", fn, {"probe": "attention", "targets": len(targets)}, hn.client.stats)


# ── Derivation ───────────────────────────────────────────────────────────────


def recompute(day: date | None = None, *, use_llm_topics: bool = False, formation: bool = True) -> dict[str, Any]:
    """classify → stage estimates → relationships → scores → formation candidates.

    Deterministic; safe to run any time. ``formation=False`` stops after scoring."""
    from radar.pipeline.stage import apply_stage_estimates
    from radar.scoring.run import score_all
    from radar.thesis.classifier import classify_entities, ensure_topics

    thesis = get_thesis()
    with session_scope() as s:
        ensure_topics(s, thesis)
        classified = classify_entities(s, thesis)
    if use_llm_topics:
        refine_topics_with_llm()
    with session_scope() as s:
        staged = apply_stage_estimates(s)
    out: dict[str, Any] = {"classified": classified, "staged": staged}
    if formation:
        out["relationships"] = build_relationships()
    with session_scope() as s:
        out["scored"] = score_all(s, day or clock.today(), thesis)
    if formation:
        out["formation"] = compute_formation(day, rebuild_relationships=False)
    return out


def build_relationships() -> dict[str, Any]:
    """Rebuild the relationships table from paper authors, contributors, memberships and affiliations."""
    from radar.pipeline import relationships

    with session_scope() as s:
        return relationships.build(s)


def compute_formation(day: date | None = None, *, rebuild_relationships: bool = True) -> dict[str, Any]:
    """FORMATION RADAR: (relationships →) formation candidates for ``day``. Reads today's score snapshots."""
    from radar.scoring import formation

    out: dict[str, Any] = {}
    if rebuild_relationships:
        out["relationships"] = build_relationships()
    with session_scope() as s:
        out.update(formation.run(s, day or clock.today()))
    return out


def refine_topics_with_llm(limit: int = 60) -> int:
    """Ask the fast model to adjudicate ambiguous thesis classifications (0.3–0.7). Cached."""
    from sqlalchemy import select

    from radar.llm.search_parse import llm_refine_topics
    from radar.models import Company, EntityTopic, Topic

    n = 0
    with session_scope() as s:
        rows = s.execute(
            select(EntityTopic, Topic, Company)
            .join(Topic, Topic.id == EntityTopic.topic_id)
            .join(Company, Company.id == EntityTopic.entity_id)
            .where(EntityTopic.method == "keyword", EntityTopic.score.between(0.3, 0.7))
            .limit(limit)
        ).all()
        for et, topic, company in rows:
            text = f"{company.name}. {company.description or ''}"
            refined = llm_refine_topics(text, [topic.key])
            if not refined:
                continue
            for key, score in refined:
                if key == topic.key:
                    # (entity, topic) is unique: upgrade the keyword row in place instead of adding a second one.
                    # method='llm' rows survive the next keyword reclassification.
                    et.score = float(score)
                    et.method = "llm"
                    et.evidence = list(dict.fromkeys([*(et.evidence or []), "llm-adjudicated"]))[:20]
                    n += 1
                    break
    return n


# ── Sweep (one-shot; used by `radar sweep`, the worker and scheduled Actions) ──────────


SWEEP_STEPS: tuple[str, ...] = ("hn", "arxiv", "github", "orgs", "enrich", "web", "stats", "probe", "score",
                                 "brief")
# 'formation' (relationships + formation candidates) runs after 'score'. The default sweep does not list it
# separately because 'score' (= recompute) already ends with it; `--only formation` refreshes just that part.
ALL_STEPS: tuple[str, ...] = ("hn", "arxiv", "github", "orgs", "enrich", "web", "stats", "probe", "score",
                              "formation", "brief", "retention")
ORGS_QUERIES_PER_RUN = 10
ORGS_MAX_PER_RUN = 60
ORGS_CREATED_WITHIN_DAYS = 180


def rotating_subset(items: list[str], n: int, day: date) -> list[str]:
    """A deterministic window of ``n`` items that rotates daily, so all terms are covered over time."""
    if len(items) <= n:
        return list(items)
    start = (day.toordinal() * n) % len(items)
    return [items[(start + i) % len(items)] for i in range(n)]


def rotation_window(items: list[str], n: int, index: int) -> list[str]:
    """Window number ``index`` of ``n`` consecutive items (wrapping), so successive runs cover every item."""
    if len(items) <= n:
        return list(items)
    start = (index * n) % len(items)
    return [items[(start + i) % len(items)] for i in range(n)]


def _young_org_runs_so_far() -> int:
    """How many young-org discovery runs were recorded before (the rotation index for the next run)."""
    from sqlalchemy import func, select

    from radar.models import IngestionRun

    with session_scope() as s:
        return int(s.scalar(
            select(func.count()).select_from(IngestionRun)
            .where(IngestionRun.provider == "github", IngestionRun.params["discovery"].as_string() == "young_orgs")
        ) or 0)


def ingest_young_orgs(*, terms: list[str] | None = None, created_within_days: int = ORGS_CREATED_WITHIN_DAYS,
                      max_orgs: int = ORGS_MAX_PER_RUN, n_terms: int = ORGS_QUERIES_PER_RUN):
    """Formation discovery: young GitHub organizations around thesis terms (pre-company projects).

    Uses ``GitHubProvider.discover_young_orgs`` (search repos created recently, keep org owners,
    filter academic/hackathon/bot orgs, require >=2 non-bot contributors with >=3 commits).
    Without explicit ``terms`` every thesis discovery query is covered: each run takes the next window of
    ``n_terms`` queries (rotation index = number of earlier young-org runs), so ⌈queries / n_terms⌉
    consecutive runs search them all while each run stays inside the GitHub search budget."""
    from radar.providers.github import GitHubProvider

    gh = GitHubProvider()
    rotation = None
    if terms:
        qs = terms
    else:
        rotation = _young_org_runs_so_far()
        qs = rotation_window(list(get_thesis().discovery_queries), n_terms, rotation)
    params = {"discovery": "young_orgs", "terms": qs, "created_within_days": created_within_days,
              "max_orgs": max_orgs, "rotation": rotation}
    if not hasattr(gh, "discover_young_orgs"):
        def missing() -> ProviderBatch:
            return ProviderBatch(provider="github", errors=["discover_young_orgs not available in this build"])

        return _run("github", missing, params, gh.client.stats)
    return _run(
        "github",
        lambda: gh.discover_young_orgs(qs, created_within_days=created_within_days, max_orgs=max_orgs),
        params,
        gh.client.stats,
    )


def run_job(name: str, fn: Callable[[], Any], params: dict[str, Any] | None = None) -> tuple[Any, Any]:
    """Run a non-fetching sweep step (score / brief / retention) recorded as IngestionRun ``job:<name>``.

    These runs never count as live data (see ``clock.is_live_run``). Returns (run, result)."""
    import time

    from radar.models import IngestionRun

    with session_scope() as s:
        run = IngestionRun(provider=f"{clock.JOB_PREFIX}{name}", status="running", params=dict(params or {}),
                           started_at=clock.wall_now())
        s.add(run)
        s.flush()
        run_id = run.id
    t0 = time.monotonic()
    result: Any = None
    error: str | None = None
    try:
        result = fn()
    except Exception as e:  # noqa: BLE001 — recorded on the run; the sweep keeps going
        error = f"{type(e).__name__}: {e}"[:500]
        log.exception("job %s failed", name)
    with session_scope() as s:
        run = s.get(IngestionRun, run_id)
        run.status = "error" if error else "ok"
        run.errors = [error] if error else []
        run.finished_at = clock.wall_now()
        run.params = {**(params or {}), "duration_s": round(time.monotonic() - t0, 3),
                      "result": _jsonable_summary(result)}
        s.flush()
        s.expunge(run)
    return run, result


def _jsonable_summary(v: Any) -> Any:
    try:
        return json.loads(json.dumps(v, default=str))
    except (TypeError, ValueError):
        return str(v)[:500]


def persist_morning_brief(day: date | None = None) -> dict[str, Any]:
    """Build today's Morning Brief and store it as ResearchReport(kind='morning_brief').

    Exactly one per day: an existing brief for the same date is replaced in place (same id)."""
    from sqlalchemy import select

    from radar.models import ResearchReport
    from radar.services.brief import morning_brief

    with session_scope() as s:
        b = morning_brief(s)
        d = day or b.date
        body = b.model_dump(mode="json")
        body["date"] = d.isoformat()
        demo = any(c.is_demo for c in [*b.founders_to_know, *b.companies_to_watch]) or any(
            p.is_demo for p in b.research) or bool(b.strange_signal and b.strange_signal.is_demo)
        existing = list(s.scalars(
            select(ResearchReport).where(ResearchReport.kind == "morning_brief",
                                         ResearchReport.body["date"].as_string() == d.isoformat())
            .order_by(ResearchReport.created_at)
        ))
        report = existing[0] if existing else ResearchReport(kind="morning_brief")
        for dup in existing[1:]:
            s.delete(dup)
        report.title = f"Morning Brief — {d:%A, %B} {d.day}, {d.year}"
        report.body = body
        report.markdown = b.markdown
        report.generator = b.generator or "template"
        report.model = None
        report.status = "complete"
        report.is_demo = demo
        report.sources = []
        if not existing:
            s.add(report)
        s.flush()
        return {"id": str(report.id), "date": d.isoformat(), "replaced": bool(existing),
                "items": len(b.what_changed), "generator": report.generator}


def run_retention() -> dict[str, Any]:
    from radar import retention

    return retention.run()


def _step_ingest(step: str) -> Any:
    if step == "hn":
        return ingest_hn()
    if step == "arxiv":
        return ingest_arxiv()
    if step == "github":
        return ingest_github()
    if step == "orgs":
        return ingest_young_orgs()
    if step == "enrich":
        return enrich_formation_anchors()
    if step == "stats":
        return retry_young_org_stats()
    if step == "probe":
        return probe_attention()
    if step == "web":
        return crawl_company_sites(limit=15)
    raise ValueError(step)


def run_step(step: str) -> dict[str, Any]:
    """Run one sweep step and summarise it. Never raises: failures are recorded and reported."""
    if step not in ALL_STEPS:
        raise ValueError(f"unknown sweep step {step!r}; expected one of {', '.join(ALL_STEPS)}")
    if step == "score":
        run, res = run_job("score", recompute)
        return _job_status(run, res)
    if step == "formation":
        run, res = run_job("formation", compute_formation)
        return _job_status(run, res)
    if step == "brief":
        run, res = run_job("brief", persist_morning_brief)
        return _job_status(run, res)
    if step == "retention":
        run, res = run_job("retention", run_retention)
        return _job_status(run, res)
    try:
        res = _step_ingest(step)
    except Exception as e:  # noqa: BLE001 — provider construction failures etc.; keep sweeping
        log.exception("sweep step %s failed", step)
        return {"status": "error", "errors": 1, "error": f"{type(e).__name__}: {e}"[:500]}
    if step == "enrich":
        return _enrich_status(res)
    if step == "stats" and res is None:
        return {"status": "ok", "signals": 0, "errors": 0, "repos": 0}
    if step == "web":
        runs = [r[0] for r in res]
        bad = sum(1 for r in runs if r.status == "error")
        return {"status": "ok" if not bad else ("error" if bad == len(runs) else "partial"), "sites": len(runs),
                "signals": sum(r.signals_created for r in runs), "errors": bad}
    return _status(res)


def sweep(steps: list[str] | tuple[str, ...] | None = None, *, github: bool = True, arxiv: bool = True,
          hn: bool = True, websites: bool = True) -> dict[str, Any]:
    """One full sweep in order (hn, arxiv, github, orgs, enrich, web, stats, probe, score, brief) or the given steps.

    Steps always run in canonical order; each is recorded in ``ingestion_runs``. The legacy
    keyword flags remove steps from the default list."""
    if steps is None:
        skip = {k for k, on in (("github", github), ("arxiv", arxiv), ("hn", hn), ("web", websites)) if not on}
        steps = [s for s in SWEEP_STEPS if s not in skip]
    wanted = list(dict.fromkeys(steps))
    for st in wanted:
        if st not in ALL_STEPS:
            raise ValueError(f"unknown sweep step {st!r}; expected one of {', '.join(ALL_STEPS)}")
    out: dict[str, Any] = {}
    for st in ALL_STEPS:
        if st in wanted:
            out[st] = run_step(st)
    return out


def _job_status(run, result) -> dict[str, Any]:
    out: dict[str, Any] = {"status": run.status, "errors": len(run.errors or [])}
    if run.errors:
        out["error"] = run.errors[0]
    if isinstance(result, dict):
        out["result"] = _jsonable_summary(result)
    return out


def _status(result) -> dict[str, Any]:
    run = result[0]
    return {"status": run.status, "signals": run.signals_created, "errors": len(run.errors or [])}


def _enrich_status(res: dict[str, Any]) -> dict[str, Any]:
    runs = [r for r, _ in res.get("runs", [])]
    bad = sum(1 for r in runs if r.status == "error")
    status = "ok" if not bad else ("error" if bad == len(runs) else "partial")
    if not runs:
        status = "ok"
    return {"status": status, "anchors": res.get("anchors", 0), "sites": res.get("sites", 0),
            "signals": sum(r.signals_created for r in runs), "errors": bad,
            "result": {k: v for k, v in res.items() if k != "runs"}}


# ── Formation-anchor enrichment (multi-source evidence for young orgs) ─────────────────


ENRICH_MAX_ANCHORS = 40
ENRICH_KINDS = ("project", "company", "stealth")
ENRICH_RECRAWL_DAYS = 3
HN_ENRICH_DAYS = 180
_MIN_LOGIN_TERM = 5  # shorter logins are too ambiguous as free-text HN search terms


def enrichment_targets(limit: int = ENRICH_MAX_ANCHORS, day: date | None = None) -> list[dict[str, Any]]:
    """Formation anchors to enrich: today's/latest company-anchored formation candidates (by score), then other
    young orgs (created <= YOUNG_DAYS ago, kind project/company/stealth, non-academic, not dismissed)."""
    from sqlalchemy import select

    from radar.models import Company, FormationCandidate, Repository
    from radar.providers.domains import company_domain, is_academic_domain
    from radar.scoring.formation import YOUNG_DAYS, _company_created

    day = day or clock.today()
    with session_scope() as s:
        cand_ids = list(dict.fromkeys(s.scalars(
            select(FormationCandidate.anchor_id)
            .where(FormationCandidate.anchor_type == "company", FormationCandidate.is_demo.is_(False))
            .order_by(FormationCandidate.score.desc())
        )))
        companies = {c.id: c for c in s.scalars(
            select(Company).where(Company.is_demo.is_(False), Company.dismissed.is_(False),
                                  Company.kind.in_(ENRICH_KINDS)))}
        repos_by: dict[Any, list[Any]] = {}
        if companies:
            for r in s.scalars(select(Repository).where(Repository.company_id.in_(list(companies)))):
                repos_by.setdefault(r.company_id, []).append(r)
        young: list[tuple[date, Any]] = []
        for c in companies.values():
            if c.id in cand_ids:
                continue
            created = _company_created(c, repos_by.get(c.id, []))
            if created is not None and 0 <= (day - created).days <= YOUNG_DAYS:
                young.append((created, c))
        young.sort(key=lambda x: (x[0], x[1].name), reverse=True)  # youngest first
        ordered = [companies[i] for i in cand_ids if i in companies] + [c for _, c in young]
        out: list[dict[str, Any]] = []
        for c in ordered[:limit]:
            repos = sorted(repos_by.get(c.id, []), key=lambda r: (-(r.stars or 0), r.full_name))
            dom = company_domain(c.website) if c.website else None
            dom = dom or c.domain
            if dom and is_academic_domain(dom):
                dom = None
            website = (c.website or "").strip() or (f"https://{dom}" if dom else None)
            if website and "://" not in website:
                website = f"https://{website}"
            out.append({
                "id": c.id, "name": c.name, "login": c.github_org, "domain": dom,
                "website": website if dom else None,
                "repos": [r.full_name for r in repos],
                "top_repo": repos[0].full_name if repos else None,
                "candidate": c.id in cand_ids,
            })
    return out


def _entity_ref(t: dict[str, Any]):
    from radar.providers.base import domain_ref, gh_org_ref

    if t.get("login"):
        return gh_org_ref(t["login"])
    return domain_ref(t["domain"]) if t.get("domain") else None


def _recently_crawled(company_id: Any, days: int) -> bool:
    from datetime import timedelta

    from sqlalchemy import select

    from radar.models import WebPage

    with session_scope() as s:
        return s.scalar(select(WebPage.id).where(
            WebPage.company_id == company_id, WebPage.last_fetched_at >= clock.wall_now() - timedelta(days=days)
        ).limit(1)) is not None


def enrich_formation_anchors(limit: int = ENRICH_MAX_ANCHORS, *, web: bool = True, hn: bool = True,
                             arxiv: bool = True, recrawl_days: int = ENRICH_RECRAWL_DAYS) -> dict[str, Any]:
    """Multi-source evidence for formation anchors (young orgs), each source recorded via ``run_provider``.

    (a) WEB/HIRING — the org's own (non-platform, non-academic) website is crawled (robots.txt respected;
        careers pages + ATS boards → jobs → ``job_posted`` / ``hiring_active``; formation language).
    (b) COMMUNITY — HN probe for the domain, org login and top repo: ``hn_probe_*`` metrics plus one
        ``hn_show``/``hn_launch``/``hn_discussion`` signal per story that actually links to the org.
    (c) RESEARCH — arXiv phrase search for ``github.com/<login>`` / the top repo name; only papers whose code
        URLs point into the org are kept (``code_for`` relations + ``paper_with_code`` on the repo, and the
        repo is attached to the org that owns it on GitHub)."""
    from radar.providers.base import ProviderBatch as _PB

    targets = enrichment_targets(limit)
    out: dict[str, Any] = {"anchors": len(targets), "candidates": sum(1 for t in targets if t["candidate"]),
                           "sites": 0, "sites_skipped_recent": 0, "runs": []}
    if not targets:
        return out

    if web:
        for t in targets:
            if not t.get("website"):
                continue
            if recrawl_days and _recently_crawled(t["id"], recrawl_days):
                out["sites_skipped_recent"] += 1
                continue
            try:
                out["runs"].append(ingest_url(t["website"], company_name=t["name"]))
                out["sites"] += 1
            except Exception as e:  # noqa: BLE001 — unsafe URL etc.; recorded where possible, keep going
                log.warning("enrich crawl failed for %s: %s", t["website"], e)

    if hn:
        from radar.providers.hackernews import HackerNewsProvider

        hnp = HackerNewsProvider()
        hn_targets = []
        for t in targets:
            ref = _entity_ref(t)
            login = t.get("login") or ""
            terms = [x for x in (t.get("domain"), t.get("top_repo"),
                                 login if len(login) >= _MIN_LOGIN_TERM else None) if x]
            if ref is not None and terms:
                hn_targets.append((ref, terms, login or None, t.get("domain")))

        def hn_fn() -> ProviderBatch:
            batch = _PB(provider="hackernews")
            for ref, terms, login, dom in hn_targets:
                batch.extend(hnp.org_attention(ref, terms, github_owner=login, domain=dom, days=HN_ENRICH_DAYS))
            return batch

        if hn_targets:
            run, _ = _run("hackernews", hn_fn, {"enrich": "formation_anchors", "targets": len(hn_targets)},
                          hnp.client.stats)
            out["runs"].append((run, None))

    if arxiv:
        from radar.providers.arxiv import ArxivProvider
        from radar.providers.base import Relation, gh_org_ref

        ax = ArxivProvider()
        ax_targets = []
        for t in targets:
            login = t.get("login")
            if not login:
                continue
            phrases = [f"github.com/{login}"]
            if t.get("top_repo"):
                name = t["top_repo"].split("/", 1)[1]
                if len(name) >= _MIN_LOGIN_TERM and name.lower() != login.lower():
                    phrases.append(name)
            ax_targets.append((login, phrases))

        def ax_fn() -> ProviderBatch:
            batch = _PB(provider="arxiv")
            for login, phrases in ax_targets:
                b = ax.search_code_for(login, phrases)
                for repo in b.repositories:  # the org owns repos under github.com/<login>/ (a fact of the URL)
                    if repo.owner.lower() == login.lower():
                        b.relations.append(Relation(kind="owns", src=gh_org_ref(login), dst=repo.ref,
                                                    source_url=f"https://github.com/{repo.full_name}"))
                batch.extend(b)
            return batch

        if ax_targets:
            run, _ = _run("arxiv", ax_fn, {"enrich": "formation_anchors", "targets": len(ax_targets)},
                          ax.client.stats)
            out["runs"].append((run, None))
    return out


def young_org_repos_missing_stats(limit: int = 80) -> list[str]:
    """Repos of young orgs (enrichment targets) with no ``commits_week`` or no ``contributors`` metric yet."""
    from sqlalchemy import select

    from radar.models import MetricSnapshot, Repository

    targets = enrichment_targets(limit=ENRICH_MAX_ANCHORS * 2)
    names = [n for t in targets for n in t["repos"]]
    if not names:
        return []
    with session_scope() as s:
        repos = {r.id: r.full_name for r in s.scalars(select(Repository).where(Repository.full_name.in_(names)))}
        have: dict[Any, set[str]] = {}
        for eid, metric in s.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.metric).distinct()
            .where(MetricSnapshot.entity_id.in_(list(repos)),
                   MetricSnapshot.metric.in_(("commits_week", "contributors")))
        ):
            have.setdefault(eid, set()).add(metric)
    order = {n: i for i, n in enumerate(names)}
    missing = [fn for rid, fn in repos.items() if have.get(rid, set()) != {"commits_week", "contributors"}]
    return sorted(missing, key=lambda n: order.get(n, 0))[:limit]


def retry_young_org_stats(limit: int = 80) -> Any:
    """Re-ask GitHub for statistics (commit activity / contributors) that answered 202 "computing" earlier."""
    from radar.providers.github import GitHubProvider

    gh = GitHubProvider()
    names = young_org_repos_missing_stats(limit)
    params = {"stats_retry": True, "repos": names}
    if not hasattr(gh, "retry_stats"):
        def missing() -> ProviderBatch:
            return ProviderBatch(provider="github", errors=["retry_stats not available in this build"])

        return _run("github", missing, params, gh.client.stats)
    if not names:
        return None  # nothing missing: no provider run (an empty run would look like fresh GitHub data)
    return _run("github", lambda: gh.retry_stats(names), params, gh.client.stats)

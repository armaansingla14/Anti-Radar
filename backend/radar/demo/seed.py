"""Demo seeding: replay the committed real-data snapshot chain (the base snapshot, then every dated
update, oldest first), optionally add the labeled fictional set, import pre-generated LLM outputs, and
derive each link on its own day: classify + score, relationship graph, FORMATION RADAR candidates
(+ formation history) and the week's Who Should We Meet list.

Each link is replayed *and derived* before the next one loads, so every day's scores and formation
candidates see only the data that existed on that day. Only the base link reconstructs score history
(days strictly before the base day); updates get a live score for their own day. ``backfill_first_seen``
runs once, after the base link (bootstrap): entities first seen in an update keep the update day.
Links already replayed into the database are skipped, so re-running applies only new updates.

    radar seed-demo                          # verified snapshot only (fictional per DEMO_SYNTHETIC)
    radar seed-demo --if-empty --no-fictional  # demo backend startup: seed once, never synthetic
    radar seed-demo --fictional              # add the clearly-labeled fictional dataset
    radar seed-demo --reset                  # wipe everything first

The fictional dataset is OFF by default (``settings.demo_synthetic``, env DEMO_SYNTHETIC).
"""

from __future__ import annotations

import gzip
import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, text

from radar import clock
from radar.db import engine, session_scope
from radar.demo.fictional import build_fictional_batch
from radar.models import Base, Company, CompanyPerson, IngestionRun, Person, Signal
from radar.pipeline.aliases import normalized_affiliations
from radar.pipeline.ingest import ingest_batch
from radar.pipeline.runner import run_provider
from radar.providers.base import ProviderBatch

log = logging.getLogger("radar.demo")

DEMO_DIR = Path(__file__).parent
SNAPSHOT_DIR = DEMO_DIR / "snapshot"
# Later verified captures (`radar demo capture --update`), replayed in date order on top of the base snapshot.
SNAPSHOT_UPDATES_DIR = DEMO_DIR / "snapshot_updates"
LLM_CACHE_FILE = DEMO_DIR / "llm_cache.jsonl"
PORTFOLIO_FILE = "portfolio.yaml"  # the portfolio config a capture used (radar demo capture --update --portfolio)


def snapshot_manifest(directory: Path = SNAPSHOT_DIR) -> dict[str, Any] | None:
    m = directory / "MANIFEST.json"
    return json.loads(m.read_text()) if m.exists() else None


def _manifest_day(directory: Path) -> date | None:
    try:
        m = snapshot_manifest(directory) or {}
        return date.fromisoformat(m["as_of"]) if m.get("as_of") else None
    except (ValueError, OSError):
        return None


def snapshot_chain(base: Path = SNAPSHOT_DIR, updates: Path = SNAPSHOT_UPDATES_DIR) -> list[Path]:
    """The base snapshot followed by every dated update, oldest first (only directories with a manifest).

    In-progress captures (``<day>.tmp``, renamed into place when complete) are never part of the chain."""
    chain = [base] if (base / "MANIFEST.json").exists() else []
    if updates.exists():
        dirs = [d for d in updates.iterdir()
                if d.is_dir() and not d.name.endswith(".tmp") and (d / "MANIFEST.json").exists()]
        chain += sorted(dirs, key=lambda d: (_manifest_day(d) or date.max, d.name))
    return chain


def snapshot_chain_manifests() -> list[dict[str, Any]]:
    return [m for d in snapshot_chain() if (m := snapshot_manifest(d))]


def latest_manifest() -> dict[str, Any] | None:
    """The manifest of the newest link in the chain (the day a DEMO_SNAPSHOT backend is pinned to)."""
    manifests = [m for m in snapshot_chain_manifests() if m.get("as_of")]
    return max(manifests, key=lambda m: m["as_of"]) if manifests else None


def reset_database() -> None:
    with engine().begin() as c:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def is_empty() -> bool:
    with session_scope() as s:
        return (s.scalar(select(func.count()).select_from(Company)) or 0) == 0


def load_snapshot(directory: Path = SNAPSHOT_DIR, *, link: int = 0, kind: str = "base") -> dict[str, Any]:
    """Replay captured provider batches through the normal ingestion pipeline (clock pinned to the
    manifest ``as_of``). Records one ``IngestionRun(provider="snapshot")`` with the link position."""
    manifest = snapshot_manifest(directory)
    if not manifest:
        return {"loaded": 0, "note": "no snapshot present"}
    as_of = date.fromisoformat(manifest["as_of"])
    clock.set_as_of(as_of)
    files = sorted(directory.glob("*.json.gz"))
    started = clock.wall_now()
    totals = {"files": 0, "signals": 0, "entities": 0}
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            payload = json.load(f)
        batch = ProviderBatch.model_validate(payload["batch"])
        meta = payload.get("run") or {}
        with session_scope() as s:
            stats = ingest_batch(s, batch)
            s.add(
                IngestionRun(
                    provider=payload["provider"],
                    status=meta.get("status", "ok"),
                    started_at=_dt(meta.get("started_at")) or started,
                    finished_at=_dt(meta.get("finished_at")) or started,
                    records_fetched=sum(batch.counts().values()),
                    records_accepted=stats.entities_created + stats.entities_updated,
                    records_rejected=batch.rejected + stats.rejected,
                    signals_created=stats.signals_created,
                    errors=meta.get("errors") or batch.errors[:20],
                    rate_limit_remaining=meta.get("rate_limit_remaining"),
                    latency_ms_p50=meta.get("latency_ms_p50"),
                    request_count=meta.get("request_count") or 0,
                    params={**(payload.get("params") or {}), "snapshot": True, "file": path.name},
                )
            )
        totals["files"] += 1
        totals["signals"] += stats.signals_created
        totals["entities"] += stats.entities_created
    with session_scope() as s:
        s.add(
            IngestionRun(
                provider="snapshot",
                status="ok",
                started_at=started,
                finished_at=clock.wall_now(),
                records_fetched=totals["files"],
                signals_created=totals["signals"],
                params={"as_of": as_of.isoformat(), "captured_at": manifest.get("captured_at"),
                        "files": totals["files"], "link": link, "kind": kind, "dir": directory.name},
            )
        )
    return {**totals, "as_of": as_of.isoformat(), "link": link, "kind": kind}


def backfill_first_seen() -> None:
    """Bootstrap only: when the radar starts from a snapshot, every entity was "seen" on capture
    day. Use the earliest public evidence instead (repo/org creation, paper date, earliest signal),
    so "Unknown Yesterday" means *appeared recently*, not "loaded today"."""
    stmts = [
        """UPDATE repositories SET first_seen_at = LEAST(first_seen_at, gh_created_at)
           WHERE is_demo = false AND gh_created_at IS NOT NULL""",
        """UPDATE research_papers SET first_seen_at = LEAST(first_seen_at, published_at)
           WHERE is_demo = false AND published_at IS NOT NULL""",
        """UPDATE companies c SET first_seen_at = LEAST(c.first_seen_at, x.t) FROM (
             SELECT company_id, MIN(gh_created_at) t FROM repositories WHERE company_id IS NOT NULL GROUP BY 1
           ) x WHERE c.id = x.company_id AND c.is_demo = false AND x.t IS NOT NULL""",
        """UPDATE companies SET first_seen_at = LEAST(first_seen_at, founded_date::timestamptz)
           WHERE is_demo = false AND founded_date IS NOT NULL""",
        """UPDATE companies c SET first_seen_at = LEAST(c.first_seen_at, x.t) FROM (
             SELECT entity_id, MIN(event_at) t FROM signals WHERE is_demo = false GROUP BY 1
           ) x WHERE c.id = x.entity_id AND c.is_demo = false""",
        """UPDATE people p SET first_seen_at = LEAST(p.first_seen_at, x.t) FROM (
             SELECT pa.person_id, MIN(rp.published_at) t FROM paper_authors pa
             JOIN research_papers rp ON rp.id = pa.paper_id GROUP BY 1
           ) x WHERE p.id = x.person_id AND p.is_demo = false AND x.t IS NOT NULL""",
        """UPDATE people p SET first_seen_at = LEAST(p.first_seen_at, x.t) FROM (
             SELECT rc.person_id, MIN(r.gh_created_at) t FROM repo_contributors rc
             JOIN repositories r ON r.id = rc.repository_id GROUP BY 1
           ) x WHERE p.id = x.person_id AND p.is_demo = false AND x.t IS NOT NULL""",
        """UPDATE people p SET first_seen_at = LEAST(p.first_seen_at, x.t) FROM (
             SELECT entity_id, MIN(event_at) t FROM signals WHERE is_demo = false GROUP BY 1
           ) x WHERE p.id = x.entity_id AND p.is_demo = false""",
    ]
    with engine().begin() as c:
        for q in stmts:
            c.execute(text(q))


def _dt(v: str | None) -> datetime | None:
    return datetime.fromisoformat(v) if v else None


def seed_fictional() -> dict[str, Any]:
    batch, post = build_fictional_batch()
    run, stats = run_provider(session_scope, "demo", lambda: batch, params={"dataset": "fictional"}, is_demo=True)
    if stats is None:
        raise RuntimeError(f"fictional seed failed: {run.errors}")
    today = clock.today()
    with session_scope() as s:
        for key, p in post["people"].items():
            person = _find_person(s, p["ref"].key)
            if person is None:
                continue
            person.prior_affiliations = p["prior"]
            person.domains = p["domains"]
            person.current_role = p.get("role")
            person.attrs = {**(person.attrs or {}), "headline": p.get("headline"), "demo": True,
                            "affiliations_normalized": normalized_affiliations(person.current_company, p["prior"])}
            person.demo_label = "Fictional example"
            person.first_seen_at = _days_ago_dt(p.get("first_seen_days_ago") or _person_first_seen(key))
            if p.get("left_days_ago") is not None:
                person.attrs = {**person.attrs, "left_prior_on": (today - timedelta(days=p["left_days_ago"])).isoformat()}
        for key, c in post["companies"].items():
            company = _find_company(s, c["ref"].key)
            if company is None:
                continue
            company.demo_label = "Fictional example"
            company.first_seen_at = _days_ago_dt(c.get("first_seen_days_ago") or COMPANY_FIRST_SEEN.get(key, 45))
            company.attrs = {**(company.attrs or {}), "demo_stage": c.get("stage")}
        # Declared topics from the dataset are stored as method='manual' (kept by the keyword classifier).
        from radar.models import EntityTopic
        from radar.thesis.classifier import ensure_topics
        from radar.thesis.loader import get_thesis

        topic_ids = ensure_topics(s, get_thesis())
        for key, c in post["companies"].items():
            company = _find_company(s, c["ref"].key)
            for tk in c.get("topics") or []:
                if company is not None and tk in topic_ids:
                    s.merge(EntityTopic(entity_type="company", entity_id=company.id, topic_id=topic_ids[tk],
                                        score=0.8, method="manual", evidence=["declared in demo dataset"]))
        # Founder roles keep their start dates relative to company formation.
        for cp in s.scalars(select(CompanyPerson).join(Company).where(Company.is_demo.is_(True))):
            cp.confidence = 1.0
        for sig in s.scalars(select(Signal).where(Signal.is_demo.is_(True), Signal.signal_type == "affiliation_changed")):
            person = s.get(Person, sig.entity_id)
            if person and person.prior_affiliations:
                sig.meta = {**(sig.meta or {}), "from": person.prior_affiliations[0], "to": person.current_company}
    return {"signals": stats.signals_created, "entities": stats.entities_created}


COMPANY_FIRST_SEEN = {"halcyon": 4, "sq-propulsion": 3, "kestrel": 38, "tessellate": 75, "perihelion": 22,
                      "orbitform": 120, "loomwork": 200}


def _person_first_seen(key: str) -> int:
    if key in {"mira-okafor", "dev-raman", "ana-lindqvist", "kenji-sato", "rafael-ortiz"}:
        return 4
    if key in {"sana-qureshi", "marcus-bell", "ilse-varga"}:
        return 3
    if key == "elias-brandt":
        return 6
    return 40


def _days_ago_dt(n: int) -> datetime:
    return clock.now() - timedelta(days=n)


def _find_person(s, key: str) -> Person | None:
    kind, _, val = key.partition(":")
    if kind == "github_user":
        return s.scalar(select(Person).where(func.lower(Person.github_handle) == val))
    return s.scalar(select(Person).where(func.lower(Person.name) == val, Person.is_demo.is_(True)))


def _find_company(s, key: str) -> Company | None:
    kind, _, val = key.partition(":")
    col = {"github_org": Company.github_org, "domain": Company.domain}.get(kind)
    if col is None:
        return None
    return s.scalar(select(Company).where(func.lower(col) == val))


def apply_demo_stages() -> None:
    """The fictional dataset declares its stage; don't let the estimator guess."""
    with session_scope() as s:
        for c in s.scalars(select(Company).where(Company.is_demo.is_(True))):
            st = (c.attrs or {}).get("demo_stage")
            if st:
                c.stage = st
                c.stage_basis = "Declared in the fictional demo dataset."


def import_llm_cache(path: Path = LLM_CACHE_FILE) -> int:
    if not path.exists():
        return 0
    from radar.llm.cache import import_cache

    return import_cache(path)


def score_history(days: tuple[int, ...] = (30, 21, 14, 10, 7, 5, 3, 2, 1), *, day: date | None = None) -> int:
    """Reconstruct past score snapshots (``day`` - d, strictly before ``day``) from backfilled histories so
    deltas are meaningful on day one, then score ``day`` live. Bootstrap only: never call it for a later
    link, or the reconstruction would overwrite earlier *live* snapshots."""
    from radar.scoring.run import score_all

    today = day or clock.today()
    n = 0
    for d in sorted({d for d in days if d > 0}, reverse=True):
        with session_scope() as s:
            score_all(s, today - timedelta(days=d), reconstruct=True)
            n += 1
    with session_scope() as s:
        score_all(s, today)
    return n + 1


def score_live(day: date) -> int:
    from radar.scoring.run import score_all

    with session_scope() as s:
        score_all(s, day)
    return 1


def seed_formation(day: date | None = None) -> dict[str, Any]:
    """Relationships + FORMATION RADAR candidates (and formation history) for ``day`` (deterministic, no LLM),
    so demo and snapshot backends show formation candidates without waiting for a scheduled run."""
    from radar.ops import compute_formation

    try:
        return compute_formation(day or clock.today())
    except Exception as e:  # noqa: BLE001 — formation is additive; never fail the whole seed over it
        log.exception("seed: formation step failed")
        return {"error": str(e)[:300]}


def seed_meet_week(day: date | None = None) -> dict[str, Any]:
    """Store (replace) the Who Should We Meet list for ``day``'s ISO week, ranked as of that day."""
    from radar.services import meet

    day = day or clock.today()
    week = meet.iso_week(datetime.combine(day, time(18, 0), tzinfo=UTC))
    try:
        with session_scope() as s:
            rows = meet.compute_week(s, week, force=True)
            return {"week": week, "people": len(rows)}
    except Exception as e:  # noqa: BLE001 — additive, like formation
        log.exception("seed: meet step failed")
        return {"week": week, "error": str(e)[:300]}


def synthetic_default() -> bool:
    """Whether the fictional dataset is included when the caller does not say (DEMO_SYNTHETIC, default False)."""
    from radar.settings import get_settings

    return bool(getattr(get_settings(), "demo_synthetic", False))


def replayed_points() -> list[date]:
    """Snapshot days already replayed into this database (IngestionRun provider="snapshot"), sorted."""
    out: set[date] = set()
    with session_scope() as s:
        for params in s.scalars(select(IngestionRun.params).where(IngestionRun.provider == "snapshot")):
            try:
                out.add(date.fromisoformat((params or {}).get("as_of") or ""))
            except ValueError:
                continue
    return sorted(out)


def derive_day(day: date, *, history: bool) -> dict[str, Any]:
    """Everything derived for one snapshot day: topics, stages, scores, relationships, formation, Meet.

    ``history=True`` (bootstrap only) reconstructs scores for days before ``day``; otherwise ``day`` is
    scored live only."""
    from radar.pipeline.stage import apply_stage_estimates
    from radar.thesis.classifier import classify_entities, ensure_topics
    from radar.thesis.loader import get_thesis

    out: dict[str, Any] = {"day": day.isoformat()}
    thesis = get_thesis()
    with session_scope() as s:
        ensure_topics(s, thesis)
        out["classified"] = classify_entities(s, thesis)
    with session_scope() as s:
        out["staged"] = apply_stage_estimates(s)
    apply_demo_stages()
    out["score_days"] = score_history(day=day) if history else score_live(day)
    out["formation"] = seed_formation(day)
    out["meet"] = seed_meet_week(day)
    return out


# ── Portfolio Pulse (V4) ─────────────────────────────────────────────────────


def sync_portfolio_file(path: Path) -> dict[str, Any]:
    from radar.portfolio.config import load_config
    from radar.portfolio.sync import sync_portfolio

    try:
        cfg = load_config(path)
        with session_scope() as s:
            return sync_portfolio(s, cfg)
    except Exception as e:  # noqa: BLE001 — a broken portfolio config never blocks the radar replay
        log.exception("seed: portfolio sync failed")
        return {"error": f"{type(e).__name__}: {e}"[:300]}


def portfolio_config_in_chain(chain: Sequence[Path] | None = None) -> Path | None:
    """The newest replayed link's captured portfolio config (never the live repo config: replay shows what was
    captured, and fixtures without a portfolio stay without one)."""
    replayed = set(replayed_points())
    found = None
    for d in chain if chain is not None else snapshot_chain():
        day = _manifest_day(d)
        if day in replayed and (d / PORTFOLIO_FILE).exists():
            found = d / PORTFOLIO_FILE
    return found


def seed_portfolio(chain: Sequence[Path] | None = None) -> dict[str, Any]:
    """Sync the newest captured portfolio config, then run the monitoring pass for the data clock's day."""
    from radar.models import PortfolioPosition
    from radar.portfolio.run import run_portfolio

    path = portfolio_config_in_chain(chain)
    out: dict[str, Any] = {}
    if path is not None:
        out["sync"] = sync_portfolio_file(path)
    with session_scope() as s:
        if s.scalar(select(func.count()).select_from(PortfolioPosition)) == 0:
            return {**out, "skipped": "no portfolio positions in the replayed snapshots"}
        out["run"] = run_portfolio(s, clock.today())
    return out


# ── Derived research layer (V3) ──────────────────────────────────────────────


def post_seed_steps(out: dict[str, Any], chain: Sequence[Path] | None = None) -> None:
    """Last step of ``seed_demo`` (clock pinned to the newest replayed day, all links derived).

    Everything here is deterministic and offline, so a reseed reproduces it exactly:
    1. captured diligence runs (``demo/diligence/*.json.gz``) are re-analysed from their recorded artifacts;
    2. Portfolio Pulse: the captured portfolio config is synced and the monitoring pass runs (events, impacts,
       questions, proxy metrics, PORTFOLIO ANALYST reviews and brief);
    3. template meeting briefs and Anti Briefs are generated from database facts (no LLM);
    4. every stored report is evaluated, documented failures are seeded, and the regression cases run.
    Each step is independent: a failure is recorded in ``out`` and never aborts the seed."""
    steps: list[tuple[str, Any]] = []

    def diligence() -> Any:
        from radar.diligence.capture import replay_all

        with session_scope() as s:
            return replay_all(s)

    def templates() -> Any:
        from radar.demo.warm import generate_template_reports

        with session_scope() as s:
            return generate_template_reports(s)

    def evaluations() -> Any:
        from radar.evals.failures import seed_documented_failures
        from radar.evals.metrics import evaluate_all
        from radar.evals.runner import run_cases, store_outcomes

        with session_scope() as s:
            evaluated = len(evaluate_all(s))
            seeded = seed_documented_failures(s)
            from radar.evals.cases import load_cases

            outcomes = run_cases(s, load_cases())
            store_outcomes(s, outcomes)
            return {"reports_evaluated": evaluated, "documented_failures": seeded,
                    "cases": len(outcomes), "cases_passed": sum(1 for o in outcomes if o.passed)}

    def portfolio() -> Any:
        return seed_portfolio(chain)

    steps = [("diligence", diligence), ("portfolio", portfolio), ("template_reports", templates),
             ("evaluations", evaluations)]
    for name, fn in steps:
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001 — additive layers never fail the seed
            log.exception("seed: %s step failed", name)
            out[name] = {"error": f"{type(e).__name__}: {e}"[:300]}


def seed_demo(*, reset: bool = False, if_empty: bool = False, snapshot: bool = True, fictional: bool | None = None,
              history: bool = True, llm_outputs: bool = True, chain: Sequence[Path] | None = None) -> dict[str, Any]:
    """Seed the database. ``if_empty`` skips everything (and only re-pins the clock) when any
    company row already exists, so it is safe to call on every container start.

    ``chain`` overrides the snapshot directories (default: ``snapshot_chain()``); each link is replayed
    with the clock pinned to its manifest day and derived on that day before the next link loads."""
    if fictional is None:
        fictional = synthetic_default()
    if if_empty and not is_empty():
        clock.init_from_db()
        return {"skipped": "database not empty"}
    if reset:
        reset_database()
    out: dict[str, Any] = {"fictional_included": fictional}
    out["llm_cache"] = import_llm_cache() if llm_outputs else 0
    links = list(chain if chain is not None else snapshot_chain()) if snapshot else []
    done = replayed_points()
    out["links"] = []
    fictional_done = False
    replayed = 0
    for i, directory in enumerate(links):
        day = _manifest_day(directory)
        info: dict[str, Any] = {"dir": directory.name, "as_of": day.isoformat() if day else None,
                                "kind": "base" if i == 0 else "update"}
        if day is None:
            info["skipped"] = "manifest without as_of"
        elif day in done:
            info["skipped"] = "already replayed"
        elif done and day < done[-1]:
            info["skipped"] = f"older than the newest replayed snapshot ({done[-1]}); run with --reset"
        if "skipped" in info:
            out["links"].append(info)
            continue
        base = not done  # the first link ever replayed into this database bootstraps it
        info["kind"] = "base" if base else "update"
        if (directory / PORTFOLIO_FILE).exists():
            # Positions and comps exactly as captured, synced before the link's batches (as during the capture).
            clock.set_as_of(day)
            info["portfolio_sync"] = sync_portfolio_file(directory / PORTFOLIO_FILE)
        info["load"] = load_snapshot(directory, link=i, kind=info["kind"])
        if base:
            backfill_first_seen()
            if fictional:
                out["fictional"] = seed_fictional()
                fictional_done = True
        info["derived"] = derive_day(day, history=history and base)
        if base:
            out["snapshot"] = info["load"]
        out["links"].append(info)
        done = sorted({*done, day})
        replayed += 1
    if not replayed:
        if done:
            # Nothing new in the chain (idempotent re-run). Only when the fictional set is asked for and not
            # yet loaded: add it and re-derive the newest replayed day live (never reconstruct over it).
            if fictional and not fictional_done:
                clock.set_as_of(done[-1])
                out["fictional"] = seed_fictional()
                derived = derive_day(done[-1], history=False)
                out["links"].append({"refreshed": derived["day"], "derived": derived})
        else:
            # No snapshot at all (tests, fictional-only): the original single-day flow on today's clock.
            if fictional:
                out["fictional"] = seed_fictional()
            derived = derive_day(clock.today(), history=history)
            out["links"].append({"refreshed": derived["day"], "derived": derived})
    derived_links = [x["derived"] for x in out["links"] if x.get("derived")]
    out["score_days"] = sum(d["score_days"] for d in derived_links)
    if derived_links:
        last = derived_links[-1]
        out["classified"], out["staged"] = last["classified"], last["staged"]
        out["formation"], out["meet"] = last["formation"], last["meet"]
    from radar.demo.warm import import_reports

    out["reports"] = import_reports() if llm_outputs else 0
    clock.init_from_db()
    post_seed_steps(out, links if snapshot else [])
    return out

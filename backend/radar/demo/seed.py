"""Demo seeding: replay the committed real-data snapshot, optionally add the labeled
fictional set, import pre-generated LLM outputs, then classify + score, then build the
relationship graph and FORMATION RADAR candidates.

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
from datetime import date, datetime, timedelta
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
LLM_CACHE_FILE = DEMO_DIR / "llm_cache.jsonl"


def snapshot_manifest(directory: Path = SNAPSHOT_DIR) -> dict[str, Any] | None:
    m = directory / "MANIFEST.json"
    return json.loads(m.read_text()) if m.exists() else None


def reset_database() -> None:
    with engine().begin() as c:
        names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
        c.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


def is_empty() -> bool:
    with session_scope() as s:
        return (s.scalar(select(func.count()).select_from(Company)) or 0) == 0


def load_snapshot(directory: Path = SNAPSHOT_DIR) -> dict[str, Any]:
    """Replay captured provider batches through the normal ingestion pipeline."""
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
                        "files": totals["files"]},
            )
        )
    return {**totals, "as_of": as_of.isoformat()}


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


def score_history(days: tuple[int, ...] = (30, 21, 14, 10, 7, 5, 3, 2, 1)) -> int:
    """Reconstruct past score snapshots from backfilled histories so deltas are meaningful on day one."""
    from radar.scoring.run import score_all

    today = clock.today()
    n = 0
    for d in sorted(days, reverse=True):
        with session_scope() as s:
            score_all(s, today - timedelta(days=d), reconstruct=True)
            n += 1
    with session_scope() as s:
        score_all(s, today)
    return n + 1


def seed_formation() -> dict[str, Any]:
    """Relationships + FORMATION RADAR candidates for the seeded day (deterministic, no LLM), so demo and
    snapshot backends show formation candidates without waiting for a scheduled run."""
    from radar.ops import compute_formation

    try:
        return compute_formation(clock.today())
    except Exception as e:  # noqa: BLE001 — formation is additive; never fail the whole seed over it
        log.exception("seed: formation step failed")
        return {"error": str(e)[:300]}


def synthetic_default() -> bool:
    """Whether the fictional dataset is included when the caller does not say (DEMO_SYNTHETIC, default False)."""
    from radar.settings import get_settings

    return bool(getattr(get_settings(), "demo_synthetic", False))


def seed_demo(*, reset: bool = False, if_empty: bool = False, snapshot: bool = True, fictional: bool | None = None,
              history: bool = True, llm_outputs: bool = True) -> dict[str, Any]:
    """Seed the database. ``if_empty`` skips everything (and only re-pins the clock) when any
    company row already exists, so it is safe to call on every container start."""
    from radar.pipeline.stage import apply_stage_estimates
    from radar.thesis.classifier import classify_entities, ensure_topics
    from radar.thesis.loader import get_thesis

    if fictional is None:
        fictional = synthetic_default()
    if if_empty and not is_empty():
        clock.init_from_db()
        return {"skipped": "database not empty"}
    if reset:
        reset_database()
    out: dict[str, Any] = {"fictional_included": fictional}
    if snapshot:
        out["snapshot"] = load_snapshot()
        backfill_first_seen()
    if fictional:
        out["fictional"] = seed_fictional()
    out["llm_cache"] = import_llm_cache() if llm_outputs else 0
    thesis = get_thesis()
    with session_scope() as s:
        ensure_topics(s, thesis)
        out["classified"] = classify_entities(s, thesis)
    with session_scope() as s:
        out["staged"] = apply_stage_estimates(s)
    apply_demo_stages()
    out["score_days"] = score_history() if history else 0
    if not history:
        from radar.scoring.run import score_all

        with session_scope() as s:
            score_all(s, clock.today())
    out["formation"] = seed_formation()
    from radar.demo.warm import import_reports

    out["reports"] = import_reports() if llm_outputs else 0
    clock.init_from_db()
    return out

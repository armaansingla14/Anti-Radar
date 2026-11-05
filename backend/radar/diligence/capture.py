"""Capture a collected run to a file and replay it offline.

A capture holds everything analysis needs — artifacts, the company facts snapshot, the collection trace and
``collected_at`` — as gzip'd JSON with sorted keys and a zero gzip mtime, so the same run always produces the
same bytes. Replaying never touches the network: it re-runs the deterministic analysis over the stored
artifacts. Captured runs for the public demo live in ``radar/demo/diligence/<slug>.json.gz``.
"""

from __future__ import annotations

import gzip
import io
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar.diligence.types import ArtifactDraft, CompanyFacts, parse_dt
from radar.models import Company, DiligenceArtifact, DiligenceRun

log = logging.getLogger(__name__)

FORMAT = "anti-radar-diligence-capture/1"
CAPTURE_DIR = Path(__file__).resolve().parent.parent / "demo" / "diligence"


class CaptureError(ValueError):
    pass


@dataclass
class Capture:
    facts: CompanyFacts
    artifacts: list[ArtifactDraft]
    collected_at: datetime
    trace: list[dict[str, Any]] = field(default_factory=list)
    partial: bool = False
    method_version: str = ""


def default_path(slug: str) -> Path:
    return CAPTURE_DIR / f"{slug}.json.gz"


def to_payload(cap: Capture) -> dict[str, Any]:
    return {
        "format": FORMAT,
        "method_version": cap.method_version,
        "collected_at": cap.collected_at.isoformat(),
        "partial": cap.partial,
        "company": cap.facts.to_dict(),
        "trace": cap.trace,
        "artifacts": [a.to_dict() for a in sorted(cap.artifacts, key=lambda a: a.key)],
    }


def dump(cap: Capture, path: Path) -> Path:
    """Write deterministically (sorted keys, compact separators, gzip mtime 0)."""
    raw = json.dumps(to_payload(cap), sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    buf = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        gz.write(raw.encode("utf-8"))
    path.write_bytes(buf.getvalue())
    return path


def load(path: Path) -> Capture:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as e:
        raise CaptureError(f"cannot read capture {path}: {e}") from e
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise CaptureError(f"{path} is not a diligence capture ({FORMAT})")
    collected = parse_dt(data.get("collected_at"))
    if collected is None:
        raise CaptureError(f"{path} has no collected_at")
    return Capture(
        facts=CompanyFacts.from_dict(data.get("company") or {}),
        artifacts=[ArtifactDraft.from_dict(a) for a in data.get("artifacts") or []],
        collected_at=collected, trace=list(data.get("trace") or []), partial=bool(data.get("partial")),
        method_version=str(data.get("method_version") or ""),
    )


def find_company(session: Session, facts: CompanyFacts) -> Company | None:
    """The company a capture belongs to: slug, then GitHub org, then domain."""
    conds = [Company.slug == facts.slug]
    if facts.github_org:
        conds.append(Company.github_org.ilike(facts.github_org))
    if facts.domain:
        conds.append(Company.domain == facts.domain)
    rows = list(session.scalars(select(Company).where(or_(*conds))))
    for pred in (lambda c: c.slug == facts.slug,
                 lambda c: facts.github_org and (c.github_org or "").lower() == facts.github_org.lower(),
                 lambda c: facts.domain and c.domain == facts.domain):
        hit = next((c for c in rows if pred(c)), None)
        if hit is not None:
            return hit
    return None


def export_run(session: Session, run_id: uuid.UUID | str, path: Path | None = None) -> Path:
    """Write a stored run's artifacts (+ company facts) to a capture file."""
    run = session.get(DiligenceRun, uuid.UUID(str(run_id)))
    if run is None:
        raise CaptureError(f"no diligence run {run_id}")
    company = session.get(Company, run.company_id)
    arts = list(session.scalars(select(DiligenceArtifact).where(DiligenceArtifact.run_id == run.id)))
    facts_d = next((t.get("facts") for t in run.trace or [] if t.get("type") == "facts"), None) or {}
    facts = CompanyFacts.from_dict(facts_d) if facts_d else CompanyFacts(name=company.name, slug=company.slug)
    cap = Capture(
        facts=facts,
        artifacts=[ArtifactDraft(key=a.key, kind=a.kind, publisher=a.publisher, url=a.url, title=a.title,
                                 fetched_at=a.fetched_at, http_status=a.http_status, content_hash=a.content_hash,
                                 text=a.text or "", data=a.data or {}, flags=list(a.flags or [])) for a in arts],
        collected_at=run.collected_at or run.started_at, trace=[t for t in run.trace or [] if t.get("type") == "fetch"],
        partial=bool((run.summary or {}).get("partial")), method_version=run.method_version,
    )
    return dump(cap, path or default_path(company.slug))


def replay(session: Session, path: Path, *, provider=None) -> DiligenceRun:
    """Recreate a run (mode=replay) from a capture file and analyse it offline."""
    from radar.diligence.run import run_diligence

    cap = load(path)
    company = find_company(session, cap.facts)
    if company is None:
        raise CaptureError(f"company {cap.facts.slug!r} from {path.name} is not in the database")
    return run_diligence(session, company, live=False, capture_path=path, provider=provider)


def replay_all(session: Session, directory: Path = CAPTURE_DIR) -> list[dict[str, Any]]:
    """Replay every capture in ``directory`` (used by the snapshot seed). Never touches the network."""
    results = []
    for path in sorted(directory.glob("*.json.gz")):
        try:
            run = replay(session, path)
            results.append({"file": path.name, "status": run.status, "run_id": str(run.id),
                            "claims": (run.summary or {}).get("claims", 0)})
        except CaptureError as e:
            log.warning("diligence replay skipped %s: %s", path.name, e)
            results.append({"file": path.name, "status": "skipped", "error": str(e)})
    return results

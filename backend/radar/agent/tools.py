"""RADAR ANALYST tools.

Every tool result is a list of ``<source>`` blocks (see :func:`radar.llm.guard.wrap_source`)
with per-run ids S1, S2, … issued by a :class:`SourceRegistry`. The registry is the
ground truth for citation validation: a claim may only cite ids registered here.
Registered sources are also persisted as ``Source`` rows.

Internal tools read the database. Live tools (GitHub, HN Algolia, arXiv, arbitrary
public URL) use :class:`HttpClient` directly and are only offered when
``live_fetch`` is enabled.
"""

from __future__ import annotations

import hashlib
import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import feedparser
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.llm.guard import detect_injection, wrap_source
from radar.llm.narratives import recent_signals, related_entity_ids
from radar.llm.provider import ToolDef
from radar.models import (
    Company,
    CompanyPerson,
    FieldProvenance,
    MetricSnapshot,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    ScoreSnapshot,
    Signal,
    Source,
)
from radar.providers.http import HttpClient, RobotsCache, UnsafeURLError, sanitize_url
from radar.settings import get_settings

log = logging.getLogger(__name__)

MAX_FETCH_CHARS = 6000
BLOCK_CHARS = 4000
_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]{1,100}/[A-Za-z0-9_.\-]{1,100}$")


class ToolInputError(ValueError):
    """Invalid tool arguments or an entity that cannot be resolved; reported to the model as is_error."""


# ── Source registry ──────────────────────────────────────────────────────────


@dataclass
class RegisteredSource:
    id: str
    url: str
    title: str
    source_type: str
    content: str
    fetched_at: datetime
    suspicious: list[str] = field(default_factory=list)

    def ref(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "fetched_at": self.fetched_at.isoformat(),
        }

    def block(self, max_chars: int = BLOCK_CHARS) -> str:
        return wrap_source(
            self.id, self.url, self.source_type, self.content, max_chars,
            title=self.title, suspicious=bool(self.suspicious),
        )


class SourceRegistry:
    """Per-run registry: issues S-ids, dedupes, detects injection attempts, persists ``Source`` rows."""

    def __init__(self, session: Session | None, *, is_demo: bool = False) -> None:
        self.session = session
        self.is_demo = is_demo
        self._by_id: dict[str, RegisteredSource] = {}
        self._by_key: dict[str, str] = {}

    def register(
        self,
        *,
        url: str,
        title: str,
        source_type: str,
        content: str,
        key: str | None = None,
        fetched_at: datetime | None = None,
        http_status: int | None = None,
    ) -> RegisteredSource:
        dedupe = key or f"{url}|{title}"
        if dedupe in self._by_key:
            return self._by_id[self._by_key[dedupe]]
        sid = f"S{len(self._by_id) + 1}"
        suspicious = detect_injection(f"{title}\n{content}")
        src = RegisteredSource(
            id=sid, url=url, title=title, source_type=source_type, content=content,
            fetched_at=fetched_at or clock.now(), suspicious=suspicious,
        )
        self._by_id[sid] = src
        self._by_key[dedupe] = sid
        self._persist(src, http_status)
        return src

    def _persist(self, src: RegisteredSource, http_status: int | None) -> None:
        if self.session is None:
            return
        h = hashlib.sha256(src.content.encode("utf-8", "ignore")).hexdigest()
        url = src.url[:2000]
        try:
            with self.session.begin_nested():
                exists = self.session.scalar(select(Source.id).where(Source.url == url, Source.content_hash == h))
                if exists is None:
                    self.session.add(
                        Source(
                            url=url, source_type=src.source_type[:40], title=src.title,
                            snippet=src.content[:500], content_hash=h, http_status=http_status,
                            fetched_at=src.fetched_at, is_demo=self.is_demo,
                        )
                    )
        except Exception as e:  # noqa: BLE001 — persistence is best-effort; the run continues
            log.warning("could not persist source %s: %s", url, e)

    def get(self, sid: str) -> RegisteredSource | None:
        return self._by_id.get(sid)

    def id_for_key(self, key: str) -> str | None:
        return self._by_key.get(key)

    def ids(self) -> set[str]:
        return set(self._by_id)

    def all(self) -> list[RegisteredSource]:
        return list(self._by_id.values())

    def refs(self) -> list[dict[str, Any]]:
        return [s.ref() for s in self._by_id.values()]

    def flags(self) -> list[dict[str, Any]]:
        return [{"id": s.id, "url": s.url, "phrases": s.suspicious} for s in self._by_id.values() if s.suspicious]

    def blocks(self, *, max_total_chars: int = 90_000, per_source: int = BLOCK_CHARS) -> str:
        """All registered sources as blocks, in id order, within a total character budget."""
        out: list[str] = []
        used = 0
        for s in self._by_id.values():
            b = s.block(per_source)
            if used + len(b) > max_total_chars:
                out.append(f"[{len(self._by_id) - len(out)} further sources omitted for length]")
                break
            out.append(b)
            used += len(b)
        return "\n\n".join(out)


# ── Gathered data containers (used by tools and the deterministic template) ──


@dataclass
class MetricWindow:
    entity_id: uuid.UUID
    label: str  # e.g. "acme/robot-os" or company name
    metric: str
    latest: float
    latest_day: date
    delta_7d: float | None
    delta_30d: float | None
    pct_30d: float | None

    def line(self) -> str:
        parts = [f"{self.label} · {self.metric}: {_num(self.latest)} on {self.latest_day.isoformat()}"]
        if self.delta_7d is not None:
            parts.append(f"7d change {_signed(self.delta_7d)}")
        if self.delta_30d is not None:
            pct = f" ({self.pct_30d:+.1f}%)" if self.pct_30d is not None else ""
            parts.append(f"30d change {_signed(self.delta_30d)}{pct}")
        return "; ".join(parts)


@dataclass
class Gathered:
    """Everything the deterministic pre-gather collected, with the source id of each item."""

    entity: Company | Person
    entity_type: str
    profile_sid: str
    provenance_sids: list[str] = field(default_factory=list)
    repos: list[tuple[Repository, str]] = field(default_factory=list)
    signals: list[tuple[Signal, str]] = field(default_factory=list)
    metrics: list[MetricWindow] = field(default_factory=list)
    metrics_sid: str | None = None
    score: ScoreSnapshot | None = None
    people: list[tuple[Person, CompanyPerson | None, str]] = field(default_factory=list)
    papers: list[tuple[ResearchPaper, str]] = field(default_factory=list)


def _num(v: float) -> str:
    return f"{int(v):,}" if float(v).is_integer() else f"{v:,.2f}"


def _signed(v: float) -> str:
    return ("+" if v >= 0 else "-") + _num(abs(v))


def _iso(d: datetime | date | None) -> str:
    return d.isoformat()[:10] if d else "unknown"


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"


# ── Entity helpers ───────────────────────────────────────────────────────────


def resolve_entity(session: Session, entity_type: str, ref: str) -> Company | Person:
    """Resolve by UUID, slug, GitHub org/handle or exact (case-insensitive) name."""
    ref = (ref or "").strip()
    if not ref:
        raise ToolInputError("entity reference is empty")
    model = {"company": Company, "person": Person}.get(entity_type)
    if model is None:
        raise ToolInputError(f"unsupported entity_type {entity_type!r}")
    try:
        obj = session.get(model, uuid.UUID(ref))
        if obj is not None:
            return obj
    except ValueError:
        pass
    handle_col = Company.github_org if model is Company else Person.github_handle
    obj = session.scalar(
        select(model).where(or_(model.slug == ref.lower(), handle_col.ilike(ref), model.name.ilike(ref))).limit(1)
    )
    if obj is None:
        raise ToolInputError(f"no {entity_type} found for {ref!r}")
    return obj


def latest_score(session: Session, entity_id: uuid.UUID) -> ScoreSnapshot | None:
    return session.scalar(
        select(ScoreSnapshot).where(ScoreSnapshot.entity_id == entity_id).order_by(ScoreSnapshot.day.desc()).limit(1)
    )


def _value_on_or_before(points: list[tuple[date, float]], day: date) -> float | None:
    best = None
    for d, v in points:
        if d <= day:
            best = v
    return best


def metric_windows(session: Session, entity: Company | Person) -> list[MetricWindow]:
    ids = related_entity_ids(session, entity)
    labels: dict[uuid.UUID, str] = {entity.id: entity.name}
    for r in session.scalars(select(Repository).where(Repository.id.in_(ids[1:]))):
        labels[r.id] = r.full_name
    rows = session.execute(
        select(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value)
        .where(MetricSnapshot.entity_id.in_(ids), MetricSnapshot.day <= clock.today())
        .order_by(MetricSnapshot.day)
    ).all()
    series: dict[tuple[uuid.UUID, str], list[tuple[date, float]]] = defaultdict(list)
    for eid, metric, day, value in rows:
        series[(eid, metric)].append((day, value))
    out: list[MetricWindow] = []
    for (eid, metric), pts in sorted(series.items(), key=lambda kv: (labels.get(kv[0][0], ""), kv[0][1])):
        last_day, last = pts[-1]
        v7 = _value_on_or_before(pts, last_day - timedelta(days=7))
        v30 = _value_on_or_before(pts, last_day - timedelta(days=30))
        d30 = last - v30 if v30 is not None else None
        out.append(
            MetricWindow(
                entity_id=eid, label=labels.get(eid, str(eid)), metric=metric, latest=last, latest_day=last_day,
                delta_7d=last - v7 if v7 is not None else None, delta_30d=d30,
                pct_30d=(d30 / v30 * 100) if (d30 is not None and v30) else None,
            )
        )
    return out


# ── Tools ────────────────────────────────────────────────────────────────────


def _schema(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}


_ENTITY_TYPE = {"type": "string", "enum": ["company", "person"]}
_ENTITY = {"type": "string", "description": "Entity id (UUID), slug, GitHub handle/org, or exact name."}

INTERNAL_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        "get_entity",
        "Profile of a company or person from the ANTI RADAR database: descriptive fields, field-level provenance "
        "and linked GitHub repositories (with README excerpts).",
        _schema({"entity_type": _ENTITY_TYPE, "entity": _ENTITY}),
    ),
    ToolDef(
        "list_signals",
        "Dated signals observed for the entity (and its repositories) in the last N days, newest first.",
        _schema({"entity_type": _ENTITY_TYPE, "entity": _ENTITY,
                 "days": {"type": "integer", "description": "Look-back window in days (1-365)."}}),
    ),
    ToolDef(
        "get_metrics",
        "Time-series metrics (latest value, 7d and 30d change) and the latest Anti Score components.",
        _schema({"entity_type": _ENTITY_TYPE, "entity": _ENTITY}),
    ),
    ToolDef(
        "get_people",
        "People linked to a company (founders, team) with roles, prior affiliations and links.",
        _schema({"company": _ENTITY}),
    ),
    ToolDef(
        "get_papers",
        "Research papers linked to the entity (company papers, or papers authored by the person/founders).",
        _schema({"entity_type": _ENTITY_TYPE, "entity": _ENTITY}),
    ),
    ToolDef(
        "search_internal",
        "Case-insensitive search over companies, people, repositories and papers in the database. "
        "Use to find related or competing entities.",
        _schema({"query": {"type": "string"}}),
    ),
)

LIVE_TOOLS: tuple[ToolDef, ...] = (
    ToolDef(
        "fetch_github_repo",
        "Fetch live GitHub metadata and README for a repository.",
        _schema({"full_name": {"type": "string", "description": "owner/name"}}),
    ),
    ToolDef(
        "search_hn",
        "Search Hacker News stories (Algolia) for a query; returns titles, points, comments, dates.",
        _schema({"query": {"type": "string"}}),
    ),
    ToolDef(
        "search_arxiv",
        "Search arXiv for recent papers matching a query; returns titles, authors, dates, abstracts.",
        _schema({"query": {"type": "string"}}),
    ),
    ToolDef(
        "fetch_url",
        "Fetch the readable text of a public web page (respects robots.txt; max 6000 characters).",
        _schema({"url": {"type": "string"}}),
    ),
)


class AnalystTools:
    """Executes analyst tools against the DB and (optionally) the public web."""

    def __init__(self, session: Session, registry: SourceRegistry, *, live_fetch: bool = False) -> None:
        self.session = session
        self.registry = registry
        self.live_fetch = live_fetch
        self._clients: dict[str, HttpClient] = {}
        self.last_ids: list[str] = []

    # ── dispatch ──
    def specs(self) -> list[ToolDef]:
        return list(INTERNAL_TOOLS) + (list(LIVE_TOOLS) if self.live_fetch else [])

    def execute(self, name: str, args: dict[str, Any]) -> tuple[str, bool]:
        """Run a tool; returns (content, is_error). Never raises."""
        self.last_ids = []
        handlers = {
            "get_entity": lambda a: self._profile_sources(self.resolve(a)),
            "list_signals": lambda a: [s for _, s in self.signals(self.resolve(a), self._days(a))],
            "get_metrics": lambda a: [s for s in [self.metrics(self.resolve(a))[2]] if s is not None],
            "get_people": lambda a: [s for *_, s in self.people(resolve_entity(self.session, "company",
                                                                                 self._str(a, "company")))],
            "get_papers": lambda a: [s for _, s in self.papers(self.resolve(a))],
            "search_internal": lambda a: self.search_internal(self._str(a, "query")),
        }
        if self.live_fetch:
            handlers.update({
                "fetch_github_repo": lambda a: self.fetch_github_repo(self._str(a, "full_name")),
                "search_hn": lambda a: self.search_hn(self._str(a, "query")),
                "search_arxiv": lambda a: self.search_arxiv(self._str(a, "query")),
                "fetch_url": lambda a: self.fetch_url(self._str(a, "url")),
            })
        handler = handlers.get(name)
        if handler is None:
            return f"Unknown or disabled tool: {name}", True
        try:
            sources = handler(args or {})
        except (ToolInputError, UnsafeURLError) as e:
            return f"Error: {e}", True
        except Exception as e:  # noqa: BLE001 — network/parse failures go back to the model
            log.warning("tool %s failed: %s", name, e)
            return f"Error: {type(e).__name__}: {e}", True
        self.last_ids = [s.id for s in sources]
        if not sources:
            return "No results.", False
        header = f"{len(sources)} source block(s): {', '.join(self.last_ids)}"
        return header + "\n\n" + "\n\n".join(s.block() for s in sources), False

    def close(self) -> None:
        for c in self._clients.values():
            c.close()
        self._clients.clear()

    # ── argument helpers ──
    @staticmethod
    def _str(args: dict[str, Any], key: str, max_len: int = 300) -> str:
        v = args.get(key)
        if not isinstance(v, str) or not v.strip():
            raise ToolInputError(f"'{key}' must be a non-empty string")
        return v.strip()[:max_len]

    @staticmethod
    def _days(args: dict[str, Any]) -> int:
        d = args.get("days", 90)
        if not isinstance(d, int) or isinstance(d, bool):
            raise ToolInputError("'days' must be an integer")
        return max(1, min(d, 365))

    def resolve(self, args: dict[str, Any]) -> Company | Person:
        return resolve_entity(self.session, self._str(args, "entity_type"), self._str(args, "entity"))

    # ── internal data ──
    def profile(self, entity: Company | Person) -> tuple[RegisteredSource, list[RegisteredSource],
                                                         list[tuple[Repository, RegisteredSource]]]:
        """(profile source, provenance sources, repositories with their sources)."""
        if isinstance(entity, Company):
            lines = [
                f"Company: {entity.name}", f"Kind: {entity.kind}", f"Stage estimate: {entity.stage}"
                + (f" (basis: {entity.stage_basis})" if entity.stage_basis else ""),
                f"Description: {entity.description or 'Unknown'}", f"Website: {entity.website or 'Unknown'}",
                f"Founded: {_iso(entity.founded_date)}", f"Location: {entity.location or 'Unknown'}",
                f"GitHub org: {entity.github_org or 'Unknown'}", f"X handle: {entity.x_handle or 'Unknown'}",
                f"Thesis tags: {', '.join(entity.thesis_tags or []) or 'none'}",
                f"First seen on radar: {_iso(entity.first_seen_at)}",
            ]
            url = f"radar://company/{entity.slug}"
            etype = "company"
        else:
            lines = [
                f"Person: {entity.name}", f"Bio: {entity.bio or 'Unknown'}",
                f"Location: {entity.location or 'Unknown'}",
                f"Current: {entity.current_role or 'Unknown role'} at {entity.current_company or 'Unknown'}",
                f"Prior affiliations: {', '.join(entity.prior_affiliations or []) or 'Unknown'}",
                f"Domains: {', '.join(entity.domains or []) or 'Unknown'}",
                f"GitHub: {entity.github_handle or 'Unknown'}", f"X: {entity.x_handle or 'Unknown'}",
                f"Personal site: {entity.personal_site or 'Unknown'}",
                f"Followers: {entity.followers if entity.followers is not None else 'Unknown'}",
                f"First seen on radar: {_iso(entity.first_seen_at)}",
            ]
            url = f"radar://person/{entity.slug}"
            etype = "person"
        if entity.is_demo:
            lines.append(f"NOTE: synthetic demo record ({entity.demo_label or 'demo'}).")
        prof = self.registry.register(
            url=url, title=f"ANTI RADAR profile: {entity.name}", source_type="demo" if entity.is_demo else "system",
            content="\n".join(lines), key=f"profile:{entity.id}",
        )

        by_url: dict[str, list[FieldProvenance]] = defaultdict(list)
        for p in self.session.scalars(
            select(FieldProvenance).where(FieldProvenance.entity_id == entity.id).order_by(FieldProvenance.field)
        ):
            by_url[p.source_url or f"radar://{etype}/{entity.slug}#provenance"].append(p)
        prov_srcs = []
        for purl, provs in by_url.items():
            content = "\n".join(
                f"{p.field}: {p.value} (epistemic {p.epistemic}, confidence {p.confidence:.2f}, "
                f"updated {_iso(p.last_updated)})" for p in provs
            )
            prov_srcs.append(self.registry.register(
                url=purl, title=f"Field provenance for {entity.name}", source_type=provs[0].source_type,
                content=content, key=f"prov:{entity.id}:{purl}",
            ))

        if isinstance(entity, Company):
            repos = list(self.session.scalars(
                select(Repository).where(Repository.company_id == entity.id).order_by(Repository.stars.desc()).limit(8)
            ))
        else:
            contributed = select(RepoContributor.repository_id).where(RepoContributor.person_id == entity.id)
            conds = [Repository.id.in_(contributed)]
            if entity.github_handle:
                conds.append(Repository.owner.ilike(entity.github_handle))
            repos = list(self.session.scalars(
                select(Repository).where(or_(*conds)).order_by(Repository.stars.desc()).limit(8)
            ))
        return prof, prov_srcs, [(r, self._repo_source(r)) for r in repos]

    def _profile_sources(self, entity: Company | Person) -> list[RegisteredSource]:
        prof, prov, repos = self.profile(entity)
        return [prof, *prov, *[s for _, s in repos]]

    def _repo_source(self, r: Repository) -> RegisteredSource:
        content = "\n".join([
            f"Repository: {r.full_name}", f"Description: {r.description or 'none'}",
            (f"Language: {r.language or 'unknown'} | Stars: {r.stars:,} | Forks: {r.forks:,} | "
            f"Contributors: {r.contributors_count if r.contributors_count is not None else 'unknown'}"),
            f"Created: {_iso(r.gh_created_at)} | Last push: {_iso(r.pushed_at)} | Archived: {r.archived}",
            f"Topics: {', '.join(r.topics or []) or 'none'} | License: {r.license or 'unknown'}",
            f"README excerpt:\n{(r.readme_excerpt or 'none')[:2500]}",
        ])
        return self.registry.register(
            url=f"https://github.com/{r.full_name}", title=f"GitHub repository {r.full_name}",
            source_type="demo" if r.is_demo else "github", content=content, key=f"repo:{r.id}",
        )

    def signals(self, entity: Company | Person, days: int = 90, limit: int = 40) -> list[tuple[Signal, RegisteredSource]]:
        rows = recent_signals(self.session, entity, days=days, limit=limit)
        return [(s, self.signal_source(s)) for s in rows]

    def signal_source(self, s: Signal) -> RegisteredSource:
        lines = [
            f"Signal: {s.title}",
            (f"Type: {s.signal_type} | Source: {s.source_type} | Event date: {_iso(s.event_at)} | "
            f"Epistemic: {s.epistemic} | Confidence: {s.confidence:.2f}"),
        ]
        if s.raw_value is not None:
            lines.append(f"Value: {_num(s.raw_value)}")
        if s.description:
            lines.append(s.description)
        if s.is_demo:
            lines.append("NOTE: synthetic demo signal.")
        return self.registry.register(
            url=s.source_url or f"radar://signal/{s.id}", title=s.title, source_type=s.source_type,
            content="\n".join(lines), key=f"signal:{s.id}",
        )

    def metrics(self, entity: Company | Person) -> tuple[list[MetricWindow], ScoreSnapshot | None,
                                                        RegisteredSource | None]:
        windows = metric_windows(self.session, entity)
        score = latest_score(self.session, entity.id)
        if not windows and score is None:
            return windows, score, None
        lines = [w.line() for w in windows[:40]]
        if score is not None:
            lines.append(f"Anti Score {score.score:.0f}/100 computed {score.day.isoformat()} (version {score.version})")
            for key, comp in (score.components or {}).items():
                if isinstance(comp, dict):
                    notes = "; ".join(str(n) for n in (comp.get("notes") or [])[:2])
                    lines.append(f"  component {key}: {comp.get('value')}/{comp.get('max')}" + (f" — {notes}" if notes else ""))
            col = score.collision or {}
            if col.get("summary"):
                lines.append(f"Signal collision: {col['summary']}")
            att = score.attention or {}
            if att:
                lines.append(f"Attention gap: technical {att.get('technical')} vs attention {att.get('attention')} "
                             f"(flagged={att.get('flagged')}, coverage={att.get('coverage')})")
        src = self.registry.register(
            url=f"radar://metrics/{entity.id}", title=f"ANTI RADAR metrics for {entity.name}", source_type="system",
            content="\n".join(lines), key=f"metrics:{entity.id}",
        )
        return windows, score, src

    def people(self, company: Company | Person) -> list[tuple[Person, CompanyPerson | None, RegisteredSource]]:
        if not isinstance(company, Company):
            raise ToolInputError("get_people expects a company")
        rows = self.session.execute(
            select(Person, CompanyPerson).join(CompanyPerson, CompanyPerson.person_id == Person.id)
            .where(CompanyPerson.company_id == company.id)
            .order_by(CompanyPerson.is_founder.desc(), Person.name)
        ).all()
        out = []
        for p, cp in rows:
            lines = [
                f"Person: {p.name}", (f"Role at {company.name}: {cp.role or 'unknown'} | Founder: {cp.is_founder} | "
                f"Link confidence: {cp.confidence:.2f}"),
                f"Tenure: {_iso(cp.start_at)} – {_iso(cp.end_at) if cp.end_at else 'present'}",
                f"Bio: {p.bio or 'Unknown'}",
                f"Current (self-reported): {p.current_role or 'Unknown'} at {p.current_company or 'Unknown'}",
                f"Prior affiliations: {', '.join(p.prior_affiliations or []) or 'Unknown'}",
                (f"GitHub: {p.github_handle or 'Unknown'} | X: {p.x_handle or 'Unknown'} | "
                f"Site: {p.personal_site or 'Unknown'}"),
            ]
            url = cp.source_url or (f"https://github.com/{p.github_handle}" if p.github_handle
                                    else f"radar://person/{p.slug}")
            src = self.registry.register(
                url=url, title=f"Person profile: {p.name}", source_type="demo" if p.is_demo else "system",
                content="\n".join(lines), key=f"person:{p.id}:{company.id}",
            )
            out.append((p, cp, src))
        return out

    def papers(self, entity: Company | Person, limit: int = 10) -> list[tuple[ResearchPaper, RegisteredSource]]:
        if isinstance(entity, Company):
            founders = select(CompanyPerson.person_id).where(CompanyPerson.company_id == entity.id)
            authored = select(PaperAuthor.paper_id).where(PaperAuthor.person_id.in_(founders))
            cond = or_(ResearchPaper.company_id == entity.id, ResearchPaper.id.in_(authored))
        else:
            cond = ResearchPaper.id.in_(select(PaperAuthor.paper_id).where(PaperAuthor.person_id == entity.id))
        rows = self.session.scalars(
            select(ResearchPaper).where(cond).order_by(ResearchPaper.published_at.desc().nulls_last()).limit(limit)
        ).all()
        return [(p, self._paper_source(p)) for p in rows]

    def _paper_source(self, p: ResearchPaper) -> RegisteredSource:
        authors = ", ".join(a.get("name", "?") for a in (p.authors or [])[:12] if isinstance(a, dict))
        content = "\n".join([
            f"Paper: {p.title}", (f"arXiv: {p.arxiv_id} | Published: {_iso(p.published_at)} | "
            f"Categories: {', '.join(p.categories or [])}"),
            f"Authors: {authors or 'Unknown'}", f"Code: {', '.join(p.code_urls or []) or 'none listed'}",
            f"Abstract: {p.abstract or 'none'}",
        ])
        return self.registry.register(
            url=p.url or f"https://arxiv.org/abs/{p.arxiv_id}", title=p.title,
            source_type="demo" if p.is_demo else "arxiv", content=content, key=f"paper:{p.id}",
        )

    def search_internal(self, query: str) -> list[RegisteredSource]:
        pat = _like(query[:120])
        lines: list[str] = []
        for c in self.session.scalars(select(Company).where(or_(Company.name.ilike(pat),
                                                                Company.description.ilike(pat))).limit(6)):
            lines.append(f"company {c.slug} (id {c.id}): {c.name} — {(c.description or '')[:160]}")
        for p in self.session.scalars(select(Person).where(or_(Person.name.ilike(pat), Person.bio.ilike(pat))).limit(6)):
            lines.append(f"person {p.slug} (id {p.id}): {p.name} — {(p.bio or '')[:160]}")
        for r in self.session.scalars(select(Repository).where(or_(Repository.full_name.ilike(pat),
                                                                   Repository.description.ilike(pat))).limit(6)):
            lines.append(f"repository {r.full_name}: {r.stars:,} stars — {(r.description or '')[:160]}")
        for pp in self.session.scalars(select(ResearchPaper).where(or_(ResearchPaper.title.ilike(pat),
                                                                       ResearchPaper.abstract.ilike(pat))).limit(6)):
            lines.append(f"paper arXiv:{pp.arxiv_id} ({_iso(pp.published_at)}): {pp.title}")
        if not lines:
            return []
        return [self.registry.register(
            url=f"radar://search?q={query[:200]}", title=f"Internal search: {query[:100]}", source_type="system",
            content="\n".join(lines), key=f"search:{query.lower()}",
        )]

    # ── live data ──
    def _client(self, name: str) -> HttpClient:
        if name not in self._clients:
            # identity encoding: HttpClient re-wraps the already-decoded body but keeps Content-Encoding,
            # so a compressed response would fail to decode a second time.
            headers = {"Accept-Encoding": "identity"}
            interval, timeout, retries, ssrf = {
                "github": (0.3, 20.0, 2, False), "arxiv": (3.0, 30.0, 2, False), "web": (1.0, 15.0, 1, True),
            }.get(name, (1.0, 20.0, 2, False))
            if name == "github":
                headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
                token = get_settings().resolved_github_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
            self._clients[name] = HttpClient(min_interval=interval, headers=headers, timeout=timeout,
                                             max_retries=retries, guard_ssrf=ssrf)
        return self._clients[name]

    def fetch_github_repo(self, full_name: str) -> list[RegisteredSource]:
        full_name = full_name.strip().strip("/")
        if not _REPO_RE.match(full_name):
            raise ToolInputError("full_name must look like owner/name")
        gh = self._client("github")
        r = gh.get(f"https://api.github.com/repos/{full_name}")
        if r.status_code == 404:
            raise ToolInputError(f"GitHub repository {full_name} not found")
        r.raise_for_status()
        d = r.json()
        readme = ""
        rr = gh.get(f"https://api.github.com/repos/{full_name}/readme",
                    headers={"Accept": "application/vnd.github.raw"})
        if rr.status_code == 200:
            readme = rr.text
        content = "\n".join([
            f"Repository: {d.get('full_name')}", f"Description: {d.get('description') or 'none'}",
            f"Owner type: {(d.get('owner') or {}).get('type')} | Homepage: {d.get('homepage') or 'none'}",
            (f"Stars: {d.get('stargazers_count')} | Forks: {d.get('forks_count')} | Open issues: "
            f"{d.get('open_issues_count')} | Language: {d.get('language')}"),
            f"Created: {d.get('created_at')} | Last push: {d.get('pushed_at')} | Archived: {d.get('archived')}",
            (f"Topics: {', '.join(d.get('topics') or []) or 'none'} | License: "
            f"{(d.get('license') or {}).get('spdx_id') or 'unknown'}"),
            f"README:\n{readme[:MAX_FETCH_CHARS] or 'none'}",
        ])
        return [self.registry.register(
            url=d.get("html_url") or f"https://github.com/{full_name}", title=f"GitHub (live) {full_name}",
            source_type="github", content=content, key=f"live-gh:{full_name.lower()}", http_status=r.status_code,
        )]

    def search_hn(self, query: str) -> list[RegisteredSource]:
        data = self._client("hn").get_json(
            "https://hn.algolia.com/api/v1/search", params={"query": query[:200], "tags": "story", "hitsPerPage": 8}
        )
        out = []
        for h in data.get("hits", [])[:8]:
            oid = h.get("objectID")
            if not oid:
                continue
            content = "\n".join([
                f"HN story: {h.get('title') or ''}", f"Linked URL: {h.get('url') or 'none'}",
                (f"Points: {h.get('points')} | Comments: {h.get('num_comments')} | Posted: {h.get('created_at')} "
                f"by {h.get('author')}"),
            ])
            out.append(self.registry.register(
                url=f"https://news.ycombinator.com/item?id={oid}", title=h.get("title") or f"HN {oid}",
                source_type="hackernews", content=content, key=f"hn:{oid}",
            ))
        return out

    def search_arxiv(self, query: str) -> list[RegisteredSource]:
        q = re.sub(r'["\\]', " ", query)[:200].strip()
        r = self._client("arxiv").get(
            "https://export.arxiv.org/api/query",
            params={"search_query": f'all:"{q}"', "start": 0, "max_results": 6,
                    "sortBy": "submittedDate", "sortOrder": "descending"},
        )
        r.raise_for_status()
        feed = feedparser.parse(r.text)
        out = []
        for e in feed.entries[:6]:
            link = e.get("link") or e.get("id")
            if not link:
                continue
            authors = ", ".join(a.get("name", "") for a in e.get("authors", [])[:10])
            content = "\n".join([
                f"Paper: {' '.join((e.get('title') or '').split())}", f"Authors: {authors or 'Unknown'}",
                f"Published: {e.get('published', 'unknown')}",
                f"Abstract: {' '.join((e.get('summary') or '').split())[:2500]}",
            ])
            out.append(self.registry.register(
                url=link, title=" ".join((e.get("title") or link).split()), source_type="arxiv", content=content,
                key=f"arxiv:{link}",
            ))
        return out

    def fetch_url(self, url: str) -> list[RegisteredSource]:
        url = sanitize_url(url)
        client = self._client("web")
        if not RobotsCache(client).allowed(url):
            raise ToolInputError(f"robots.txt disallows fetching {url}")
        r = client.get(url)
        if r.status_code >= 400:
            raise ToolInputError(f"HTTP {r.status_code} for {url}")
        ctype = r.headers.get("content-type", "")
        if "html" in ctype:
            tree = LexborHTMLParser(r.text)
            title = (tree.css_first("title").text(strip=True) if tree.css_first("title") else "") or url
            for node in tree.css("script, style, noscript, svg, nav, footer, iframe"):
                node.decompose()
            body = tree.body
            text = " ".join((body.text(separator=" ") if body else "").split())
        elif ctype.startswith("text/") or "json" in ctype:
            title, text = url, " ".join(r.text.split())
        else:
            raise ToolInputError(f"unsupported content type {ctype or 'unknown'}")
        return [self.registry.register(
            url=str(r.request.url) if r.request else url, title=title[:300], source_type="website",
            content=text[:MAX_FETCH_CHARS], key=f"url:{url}", http_status=r.status_code,
        )]

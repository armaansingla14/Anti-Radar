"""arXiv provider: recent papers per thesis category, phrase search and id lookup.

Uses the public Atom API (https://export.arxiv.org/api/query). arXiv asks for at
most one request every 3 seconds, so the client is throttled to 3.1s per host.

Emitted records (all FACT, straight from the feed):
    PaperRecord per paper, PersonRecord per author (name-keyed, see limitations),
    RepoRecord stubs for GitHub repos linked from the abstract/comment.
Signals:
    paper_published   entity=paper   (0.4, 0.6 when code is released)
    paper_with_code   entity=repo    (repo linked from a paper)
    author_recurrence entity=person  (>= 2 papers by the same name in the window;
                      INFERENCE — the cross-paper identity is a name match)

Author refs are name-based (`arxiv_author:<normalized name>`), so common names
collide. The ref is therefore *not* an identity key: ingestion resolves each
(paper, author) pair separately and reuses an existing person only with
corroborating evidence (see `Resolver.resolve_arxiv_author`), and recurrence
signals are labelled INFERENCE with an identity caveat.
"""

from __future__ import annotations

import io
import math
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import feedparser
import httpx

from radar import clock
from radar.providers.base import (
    EntityRef,
    NormalizedSignal,
    PaperRecord,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    arxiv_author_ref,
    paper_ref,
    repo_ref,
    strip_arxiv_version,
)
from radar.providers.domains import github_repos_in
from radar.providers.http import HttpClient, content_hash
from radar.thesis.loader import get_thesis

API_URL = "https://export.arxiv.org/api/query"
SOURCE = "arxiv"
MIN_INTERVAL_S = 3.1
MAX_LINKED_AUTHORS = 30  # large collaborations: keep full author list on the paper, link only the first N
ID_BATCH = 100

_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_ABS_ID_RE = re.compile(r"arxiv\.org/abs/(.+)$", re.IGNORECASE)
_VALID_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$")
_URL_RE = re.compile(r"https?://[^\s{}\\$<>\"'|]+", re.IGNORECASE)
_URL_TRAIL = ".,;:)]}>'\""


def _collapse(text: str | None) -> str:
    return " ".join((text or "").split())


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def extract_project_urls(*texts: str | None) -> list[str]:
    """Non-GitHub-repo, non-arXiv URLs (project pages, demos) mentioned in the given texts."""
    out: list[str] = []
    for text in texts:
        for raw in _URL_RE.findall(text or ""):
            url = raw.rstrip(_URL_TRAIL)
            host = _host(url)
            if not host or host == "github.com" or host == "arxiv.org" or host.endswith(".arxiv.org"):
                continue
            if url not in out:
                out.append(url)
    return out


def _author_affiliations(xml_text: str) -> dict[str, list[dict[str, Any]]]:
    """Map entry id -> [{name, affiliation?}] (feedparser collapses per-author affiliations)."""
    if "<!DOCTYPE" in xml_text[:2000].upper():
        return {}  # never expand DTDs from a remote document
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for entry in root.findall("atom:entry", _NS):
        eid = (entry.findtext("atom:id", default="", namespaces=_NS) or "").strip()
        authors: list[dict[str, Any]] = []
        for a in entry.findall("atom:author", _NS):
            name = _collapse(a.findtext("atom:name", default="", namespaces=_NS))
            if not name:
                continue
            affs = [_collapse(x.text) for x in a.findall("arxiv:affiliation", _NS) if _collapse(x.text)]
            rec: dict[str, Any] = {"name": name}
            if affs:
                rec["affiliation"] = "; ".join(affs)
            authors.append(rec)
        if eid:
            out[eid] = authors
    return out


def _parse_entry(entry: Any, affiliations: dict[str, list[dict[str, Any]]]) -> PaperRecord | None:
    """One feedparser entry -> PaperRecord, or None when required fields are missing/malformed."""
    eid = (entry.get("id") or "").strip()
    m = _ABS_ID_RE.search(eid)
    if not m or not _VALID_ID_RE.match(m.group(1)):
        return None
    arxiv_id = strip_arxiv_version(m.group(1))
    title = _collapse(entry.get("title"))
    published = _parse_dt(entry.get("published"))
    if not title or published is None:
        return None
    abstract = _collapse(entry.get("summary")) or None
    comment = _collapse(entry.get("arxiv_comment")) or None
    categories = [t["term"] for t in entry.get("tags", []) if t.get("term")]
    primary = (entry.get("arxiv_primary_category") or {}).get("term") or (
        categories[0] if categories else None
    )
    authors = affiliations.get(eid) or [
        {"name": _collapse(a.get("name"))} for a in entry.get("authors", []) if _collapse(a.get("name"))
    ]
    repos = github_repos_in(" ".join(filter(None, [abstract, comment])))
    attrs: dict[str, Any] = {"author_count": len(authors)}
    project_urls = extract_project_urls(comment, abstract)
    if project_urls:
        attrs["project_urls"] = project_urls
    if comment:
        attrs["comment"] = comment
    if entry.get("arxiv_journal_ref"):
        attrs["journal_ref"] = _collapse(entry["arxiv_journal_ref"])
    if entry.get("arxiv_doi"):
        attrs["doi"] = entry["arxiv_doi"].strip()
    return PaperRecord(
        ref=paper_ref(arxiv_id),
        arxiv_id=arxiv_id,
        title=title,
        abstract=abstract,
        categories=categories,
        primary_category=primary,
        published_at=published,
        updated_at=_parse_dt(entry.get("updated")),
        authors=authors,
        code_urls=[f"https://github.com/{slug}" for slug in repos],
        url=f"https://arxiv.org/abs/{arxiv_id}",
        attrs=attrs,
    )


def parse_feed(
    xml_text: str,
    *,
    window_days: int | None = None,
    now: datetime | None = None,
    seen_ids: set[str] | None = None,
) -> ProviderBatch:
    """Parse an arXiv Atom response into a ProviderBatch of papers only (no derived records).

    window_days: drop papers published before now - window_days (not counted as rejected).
    seen_ids: ids already emitted by earlier pages/categories; repeats are skipped.
    """
    batch = ProviderBatch(provider=SOURCE)
    seen = seen_ids if seen_ids is not None else set()
    feed = feedparser.parse(io.BytesIO(xml_text.encode("utf-8")))
    affiliations = _author_affiliations(xml_text)
    cutoff = (now or clock.now()) - timedelta(days=window_days) if window_days is not None else None
    for entry in feed.entries:
        eid = entry.get("id") or ""
        if "/api/errors" in eid:  # arXiv reports query errors as a single entry
            batch.errors.append(f"arxiv error: {_collapse(entry.get('summary'))}")
            continue
        paper = _parse_entry(entry, affiliations)
        if paper is None:
            batch.rejected += 1
            continue
        if paper.arxiv_id in seen:
            continue
        seen.add(paper.arxiv_id)
        if cutoff is not None and paper.published_at and paper.published_at < cutoff:
            continue
        batch.papers.append(paper)
    if feed.bozo and not feed.entries:
        batch.errors.append(f"arxiv feed unparseable: {feed.get('bozo_exception')!r}")
    return batch


def enrich(batch: ProviderBatch, *, window_days: int = 21, now: datetime | None = None) -> ProviderBatch:
    """Derive people, repo stubs, relations, signals and sources from `batch.papers` (in place)."""
    now = now or clock.now()
    people: dict[str, PersonRecord] = {}
    repos: dict[str, RepoRecord] = {}
    papers_by_author: dict[str, list[PaperRecord]] = defaultdict(list)

    for paper in batch.papers:
        event_at = paper.published_at or now
        author_refs: list[EntityRef] = []
        for pos, author in enumerate(paper.authors[:MAX_LINKED_AUTHORS]):
            ref = arxiv_author_ref(author["name"])
            if not ref.key.removeprefix("arxiv_author:"):
                continue  # name with no latin letters normalizes to empty; cannot key it
            author_refs.append(ref)
            aff = author.get("affiliation")
            person = people.get(ref.key)
            if person is None:
                person = PersonRecord(ref=ref, name=author["name"], source_url=paper.url)
                people[ref.key] = person
            if aff and not person.current_company:
                person.current_company = aff
                person.attrs["arxiv_affiliation"] = aff
            batch.relations.append(
                Relation(kind="author", src=ref, dst=paper.ref, attrs={"position": pos}, source_url=paper.url)
            )
            papers_by_author[ref.key].append(paper)

        repo_refs: list[EntityRef] = []
        for code_url in paper.code_urls:
            slug = code_url.removeprefix("https://github.com/")
            rref = repo_ref(slug)
            repo_refs.append(rref)
            if rref.key not in repos:
                repos[rref.key] = RepoRecord(
                    ref=rref,
                    full_name=slug,
                    owner=slug.split("/")[0],
                    attrs={"stub": True, "discovered_via": SOURCE},
                )
            batch.relations.append(Relation(kind="code_for", src=paper.ref, dst=rref, source_url=paper.url))
            batch.signals.append(
                NormalizedSignal(
                    entity=rref,
                    signal_type="paper_with_code",
                    source_type=SOURCE,
                    source_url=paper.url,
                    title=f"Code for arXiv paper: {paper.title}",
                    description=f'{code_url} is linked as code for arXiv:{paper.arxiv_id} "{paper.title}".',
                    raw_value=1.0,
                    normalized_value=0.6,
                    event_at=event_at,
                    related=[paper.ref, *author_refs],
                    metadata={"arxiv_id": paper.arxiv_id, "code_url": code_url},
                )
            )

        n_auth = len(paper.authors)
        batch.signals.append(
            NormalizedSignal(
                entity=paper.ref,
                signal_type="paper_published",
                source_type=SOURCE,
                source_url=paper.url,
                title=paper.title,
                description=(
                    f"arXiv:{paper.arxiv_id} [{paper.primary_category or 'uncategorized'}] by {n_auth} "
                    f"author{'s' if n_auth != 1 else ''}"
                    + (f"; code: {', '.join(paper.code_urls)}" if paper.code_urls else "")
                ),
                normalized_value=0.6 if paper.code_urls else 0.4,
                event_at=event_at,
                related=[*author_refs, *repo_refs],
                metadata={
                    "arxiv_id": paper.arxiv_id,
                    "categories": paper.categories,
                    "has_code": bool(paper.code_urls),
                    "project_urls": paper.attrs.get("project_urls", []),
                },
            )
        )
        batch.sources.append(
            {
                "url": paper.url,
                "source_type": SOURCE,
                "title": paper.title,
                "snippet": (paper.abstract or "")[:500] or None,
                "content_hash": content_hash(f"{paper.title}\n{paper.abstract or ''}"),
            }
        )

    batch.signals.extend(_author_recurrence(papers_by_author, people, window_days=window_days, now=now))
    batch.people.extend(people.values())
    batch.repositories.extend(repos.values())
    return batch


IDENTITY_CAVEAT = "Author identity matched by name across papers; not verified."


def recurrence_signal(
    person_ref: EntityRef, name: str, papers: list[PaperRecord], *, window_days: int,
    metadata: dict[str, Any] | None = None,
) -> NormalizedSignal | None:
    """`author_recurrence` for one author over `papers` (already windowed); None below 2 papers.

    Always INFERENCE: that the same person wrote these papers is inferred from the name.
    """
    papers = sorted(
        {p.arxiv_id: p for p in papers if p.published_at}.values(), key=lambda p: (p.published_at, p.arxiv_id)
    )
    if len(papers) < 2:
        return None
    first, last = papers[0].published_at, papers[-1].published_at
    assert first is not None and last is not None
    span_days = max(1, math.ceil((last - first).total_seconds() / 86400))
    n = len(papers)
    return NormalizedSignal(
        entity=person_ref,
        signal_type="author_recurrence",
        source_type=SOURCE,
        source_url=papers[-1].url,
        title=f"{name}: {n} papers in {span_days} day{'s' if span_days != 1 else ''}",
        description="Recent arXiv papers: "
        + "; ".join(f"arXiv:{p.arxiv_id} {p.title}" for p in papers)
        + f". {IDENTITY_CAVEAT}",
        raw_value=float(n),
        normalized_value=min(0.85, 0.4 + 0.15 * (n - 2)),
        event_at=last,
        confidence=0.8,  # identity is name-based; homonyms can collide
        epistemic="INFERENCE",
        related=[p.ref for p in papers],
        metadata={
            "paper_ids": [p.arxiv_id for p in papers],
            "span_days": span_days,
            "window_days": window_days,
            "identity": "name_match",
            "caveat": IDENTITY_CAVEAT,
            **(metadata or {}),
        },
    )


def _author_recurrence(
    papers_by_author: dict[str, list[PaperRecord]],
    people: dict[str, PersonRecord],
    *,
    window_days: int,
    now: datetime,
) -> list[NormalizedSignal]:
    cutoff = now - timedelta(days=window_days)
    out: list[NormalizedSignal] = []
    for key in sorted(papers_by_author):
        person = people[key]
        recent = [p for p in papers_by_author[key] if p.published_at and p.published_at >= cutoff]
        sig = recurrence_signal(person.ref, person.name, recent, window_days=window_days)
        if sig is not None:
            out.append(sig)
    return out


class ArxivProvider:
    name = "arxiv"

    def __init__(
        self, client: HttpClient | None = None, mode: str = "live", cassette_dir: Path | None = None
    ):
        self.client = client or HttpClient(min_interval=MIN_INTERVAL_S, mode=mode, cassette_dir=cassette_dir)

    def enabled(self) -> tuple[bool, str]:
        return True, "public API, no credentials required"

    def _query(self, params: dict[str, Any], batch: ProviderBatch) -> str | None:
        try:
            resp = self.client.get(API_URL, params=params)
        except (httpx.HTTPError, FileNotFoundError) as e:
            batch.errors.append(f"arxiv request failed ({params}): {e}")
            return None
        if resp.status_code != 200:
            batch.errors.append(f"arxiv HTTP {resp.status_code} for {params}")
            return None
        return resp.text

    def _run(
        self, param_sets: list[dict[str, Any]], *, window_days: int | None, recurrence_days: int
    ) -> ProviderBatch:
        batch = ProviderBatch(provider=SOURCE)
        seen: set[str] = set()
        now = clock.now()
        for params in param_sets:
            text = self._query(params, batch)
            if text is not None:
                batch.extend(parse_feed(text, window_days=window_days, now=now, seen_ids=seen))
        return enrich(batch, window_days=recurrence_days, now=now)

    def fetch_recent(
        self, categories: list[str] | None = None, *, max_results: int | None = None, days: int = 21
    ) -> ProviderBatch:
        """Most recently submitted papers per category, restricted to the last `days` days."""
        cfg = get_thesis().arxiv
        cats = categories if categories is not None else cfg.categories
        n = max_results or cfg.max_results_per_category
        params = [
            {
                "search_query": f"cat:{c}",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": 0,
                "max_results": n,
            }
            for c in cats
        ]
        return self._run(params, window_days=days, recurrence_days=days)

    def search(self, query: str, max_results: int = 25, *, days: int = 21) -> ProviderBatch:
        """Phrase search across all fields (newest first). `days` only bounds author recurrence."""
        phrase = _collapse(query).replace('"', "")
        params = [
            {
                "search_query": f'all:"{phrase}"',
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "start": 0,
                "max_results": max_results,
            }
        ]
        return self._run(params, window_days=None, recurrence_days=days)

    def fetch_ids(self, ids: list[str], *, days: int = 21) -> ProviderBatch:
        """Specific papers by id (version suffixes are ignored)."""
        valid = [i.strip() for i in ids if _VALID_ID_RE.match(i.strip())]
        clean = list(dict.fromkeys(strip_arxiv_version(i) for i in valid))
        params = [
            {
                "id_list": ",".join(clean[i : i + ID_BATCH]),
                "start": 0,
                "max_results": len(clean[i : i + ID_BATCH]),
            }
            for i in range(0, len(clean), ID_BATCH)
        ]
        batch = self._run(params, window_days=None, recurrence_days=days)
        batch.rejected += len(ids) - len(valid)
        return batch

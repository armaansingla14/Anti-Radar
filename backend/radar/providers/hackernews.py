"""Hacker News provider via the Algolia HN Search API (no auth).

* fetch_launches: Show HN / Launch HN stories (attention + formation signal).
* search_topics:  stories matching thesis discovery queries.
* mentions:       attention measurement for a term (domain, repo full name, product name).

Each story is resolved to exactly one entity from its link:
    github.com/<owner>/<repo>   -> repository stub
    arxiv.org/abs|pdf/<id>      -> paper stub
    other non-platform domain   -> company (Show/Launch HN only)
Self-posts (no URL) of Show/Launch HN fall back to a link in the post text whose
domain matches the product name in the title. Generic discussion stories never
create companies from arbitrary domains (news sites, blogs are not companies).
HN usernames are kept in signal metadata only; no people are created from them.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx

from radar import clock
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    MetricPoint,
    NormalizedSignal,
    PaperRecord,
    ProviderBatch,
    RepoRecord,
    domain_ref,
    paper_ref,
    repo_ref,
    strip_arxiv_version,
)
from radar.providers.domains import company_domain, github_repos_in
from radar.providers.http import HttpClient, content_hash

SEARCH_URL = "https://hn.algolia.com/api/v1/search"
SEARCH_BY_DATE_URL = "https://hn.algolia.com/api/v1/search_by_date"
SOURCE = "hackernews"
MIN_INTERVAL_S = 0.3
HITS_PER_PAGE = 100
MAX_PAGES = 3

HNKind = Literal["launch_hn", "show_hn", "story"]
SIGNAL_TYPE: dict[str, str] = {"launch_hn": "hn_launch", "show_hn": "hn_show", "story": "hn_discussion"}

_PREFIX_RE = re.compile(r"^\s*(launch|show)\s+hn\s*(?::|[-–—]+)\s*", re.IGNORECASE)
_YC_RE = re.compile(r"\(\s*YC\s+([A-Z]{1,2})\s?(\d{2}|\d{4})\s*\)", re.IGNORECASE)
# Name/tagline separators: spaced hyphen or dash, unspaced en/em dash, colon, ", a|an|the ".
_SEP_RE = re.compile(r"\s+[-–—]+\s+|\s*[–—]\s*|:\s+|,\s+(?=(?:a|an|the)\s)", re.IGNORECASE)
_NOT_A_NAME_RE = re.compile(
    r"^(i|i'm|i've|we|we're|we've|my|our|a|an|the|how|why|what|this)\b", re.IGNORECASE
)
_MAX_NAME_WORDS = 5
_ARXIV_PATH_RE = re.compile(
    r"^/(?:abs|pdf|html)/(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)"
)
_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)
# App-hosting platforms missing from domains.PLATFORM_DOMAINS: a subdomain there does not identify a company.
HOSTING_DOMAINS = frozenset(
    {
        "fly.dev",
        "pages.dev",
        "workers.dev",
        "onrender.com",
        "railway.app",
        "replit.app",
        "repl.co",
        "glitch.me",
        "web.app",
        "firebaseapp.com",
        "azurewebsites.net",
        "cloudfront.net",
        "amplifyapp.com",
        "deno.dev",
        "surge.sh",
        "gitbook.io",
        "framer.website",
        "framer.app",
        "webflow.io",
        "carrd.co",
        "wixsite.com",
        "squarespace.com",
        "lovable.app",
        "bolt.new",
        "neocities.org",
        "codeberg.page",
        "gitlab.io",
    }
)


@dataclass(frozen=True)
class ParsedTitle:
    kind: HNKind
    product_name: str | None
    tagline: str | None
    yc_batch: str | None


def parse_hn_title(title: str) -> tuple[HNKind, str | None, str | None, str | None]:
    """'Launch HN: Acme (YC X25) – Robots' -> ('launch_hn', 'Acme', 'Robots', 'X25').

    product_name is None when the title has no recognizable "Name – tagline" shape
    (e.g. 'Show HN: I built a thing'); tagline is then the whole remainder.
    """
    t = " ".join((title or "").split())
    m = _PREFIX_RE.match(t)
    if not m:
        return "story", None, t or None, None
    kind: HNKind = "launch_hn" if m.group(1).lower() == "launch" else "show_hn"
    rest = t[m.end() :].strip()

    yc = _YC_RE.search(rest)
    if yc:
        batch = f"{yc.group(1).upper()}{yc.group(2)}"
        name = rest[: yc.start()].strip(" -–—:,")
        tagline = rest[yc.end() :].strip(" -–—:,")
        return kind, (name or None), (tagline or None), batch

    sep = _SEP_RE.search(rest)
    if not sep:
        return kind, None, rest or None, None
    name, tagline = rest[: sep.start()].strip(), rest[sep.end() :].strip()
    if not name or _NOT_A_NAME_RE.match(name) or len(name.split()) > _MAX_NAME_WORDS:
        return kind, None, rest or None, None
    return kind, name, (tagline or None), None


def _origin(url: str) -> str:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _name_matches(name: str | None, label: str) -> bool:
    """'Salem Robotics' ~ 'salemroboticsinc', 'Pumpkins.sh' ~ 'pumpkins' (alnum containment)."""
    n, lab = _alnum(name or ""), _alnum(label)
    return len(n) >= 3 and len(lab) >= 3 and (n in lab or lab in n)


def _company_domain(url: str) -> str | None:
    d = company_domain(url)
    return None if d is None or d in HOSTING_DOMAINS else d


def _github_slug(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host not in ("github.com", "www.github.com"):
        return None
    slugs = github_repos_in(url)
    return slugs[0] if slugs else None


def _arxiv_id(url: str) -> str | None:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if host != "arxiv.org" and not host.endswith(".arxiv.org"):
        return None
    m = _ARXIV_PATH_RE.match(p.path)
    return strip_arxiv_version(m.group(1).removesuffix(".pdf")) if m else None


def _text_links(story_text: str | None) -> list[str]:
    return [html.unescape(h) for h in _HREF_RE.findall(story_text or "")]


def _strip_html(text: str | None) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", text or "")).split())


@dataclass
class _Resolved:
    entity: EntityRef
    link: str
    link_source: Literal["url", "story_text"]
    company: CompanyRecord | None = None
    repo: RepoRecord | None = None
    paper: PaperRecord | None = None


def _resolve_link(
    url: str, kind: HNKind, parsed: ParsedTitle, hn_url: str, story_id: str
) -> _Resolved | None:
    slug = _github_slug(url)
    if slug:
        ref = repo_ref(slug)
        repo = RepoRecord(
            ref=ref, full_name=slug, owner=slug.split("/")[0], attrs={"stub": True, "discovered_via": SOURCE}
        )
        return _Resolved(entity=ref, link=url, link_source="url", repo=repo)
    aid = _arxiv_id(url)
    if aid:
        ref = paper_ref(aid)
        paper = PaperRecord(
            ref=ref,
            arxiv_id=aid,
            title=f"arXiv {aid}",
            url=f"https://arxiv.org/abs/{aid}",
            attrs={"stub": True, "discovered_via": SOURCE},
        )
        return _Resolved(entity=ref, link=url, link_source="url", paper=paper)
    if kind == "story":
        return None  # discussion links to arbitrary domains do not identify companies
    domain = _company_domain(url)
    if not domain:
        return None
    name = parsed.product_name or domain.split(".")[0]
    attrs: dict[str, Any] = {
        "discovered_via": SOURCE,
        "hn_story_id": story_id,
        "name_matches_domain": _name_matches(parsed.product_name, domain.split(".")[0]),
    }
    if parsed.yc_batch:
        attrs["yc_batch"] = parsed.yc_batch
    company = CompanyRecord(
        ref=domain_ref(domain),
        name=name,
        description=parsed.tagline,
        website=_origin(url),
        kind="company" if kind == "launch_hn" else "project",
        attrs=attrs,
        source_url=hn_url,
    )
    return _Resolved(entity=company.ref, link=url, link_source="url", company=company)


def resolve_story(hit: dict[str, Any]) -> tuple[ParsedTitle, _Resolved | None]:
    """Resolve an Algolia hit to the single entity it is about (or None)."""
    story_id = str(hit["objectID"])
    hn_url = f"https://news.ycombinator.com/item?id={story_id}"
    parsed = ParsedTitle(*parse_hn_title(hit.get("title") or ""))
    url = (hit.get("url") or "").strip()
    if url:
        res = _resolve_link(url, parsed.kind, parsed, hn_url, story_id)
        if res:
            return parsed, res
    if parsed.kind == "story" or not parsed.product_name:
        return parsed, None
    # Show/Launch HN self-post or platform link: accept a post-text link only if it matches the product name.
    for link in _text_links(hit.get("story_text")):
        domain = _company_domain(link)
        slug = _github_slug(link)
        label = domain.split(".")[0] if domain else (slug.split("/")[1] if slug else "")
        if label and _name_matches(parsed.product_name, label):
            res = _resolve_link(link, parsed.kind, parsed, hn_url, story_id)
            if res:
                res.link_source = "story_text"
                return parsed, res
    return parsed, None


def _created_at(hit: dict[str, Any]) -> datetime | None:
    ts = hit.get("created_at_i")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=UTC)
    raw = hit.get("created_at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    return None


def _link_confidence(parsed: ParsedTitle, res: _Resolved) -> float:
    """How sure we are the story is about the resolved entity."""
    if res.link_source == "story_text":
        return 0.8
    if res.company and parsed.product_name and not res.company.attrs.get("name_matches_domain"):
        return 0.7
    return 1.0


def hn_strength(points: int) -> float:
    return min(1.0, math.log10(max(points, 0) + 1) / 3)


def build_batch(hits: list[dict[str, Any]], *, now: datetime | None = None) -> ProviderBatch:
    """Turn Algolia story hits into records, signals and hn_points metrics (deduplicated by story id)."""
    now = now or clock.now()
    batch = ProviderBatch(provider=SOURCE)
    seen: set[str] = set()
    companies: dict[str, CompanyRecord] = {}
    repos: dict[str, RepoRecord] = {}
    papers: dict[str, PaperRecord] = {}
    points_by_day: dict[tuple[EntityRef, date], float] = {}

    for hit in hits:
        sid = str(hit.get("objectID") or "")
        created = _created_at(hit)
        if not sid or not hit.get("title") or created is None:
            batch.rejected += 1
            continue
        if sid in seen:
            continue
        seen.add(sid)
        parsed, res = resolve_story(hit)
        if res is None:
            batch.rejected += 1
            continue
        if res.company and res.entity.key not in companies:
            companies[res.entity.key] = res.company
        if res.repo and res.entity.key not in repos:
            repos[res.entity.key] = res.repo
        if res.paper and res.entity.key not in papers:
            papers[res.entity.key] = res.paper

        hn_url = f"https://news.ycombinator.com/item?id={sid}"
        points = int(hit.get("points") or 0)
        comments = int(hit.get("num_comments") or 0)
        age_h = (now - created).total_seconds() / 3600
        velocity = round(comments / max(age_h, 1.0), 3) if age_h > 0 else None
        batch.signals.append(
            NormalizedSignal(
                entity=res.entity,
                signal_type=SIGNAL_TYPE[parsed.kind],
                source_type=SOURCE,
                source_url=hn_url,
                title=" ".join(hit["title"].split()),
                description=f"{points} points, {comments} comments on Hacker News; links to {res.link}",
                raw_value=float(points),
                normalized_value=hn_strength(points),
                event_at=created,
                confidence=_link_confidence(parsed, res),
                metadata={
                    "points": points,
                    "num_comments": comments,
                    "author": hit.get("author"),
                    "story_id": sid,
                    "comment_velocity": velocity,
                    "link": res.link,
                    "link_source": res.link_source,
                    "product_name": parsed.product_name,
                    "tagline": parsed.tagline,
                    "yc_batch": parsed.yc_batch,
                },
            )
        )
        key = (res.entity, created.date())
        points_by_day[key] = points_by_day.get(key, 0.0) + points
        snippet = _strip_html(hit.get("story_text"))[:500] or res.link
        batch.sources.append(
            {
                "url": hn_url,
                "source_type": SOURCE,
                "title": hit["title"],
                "snippet": snippet,
                "content_hash": content_hash(f"{hit['title']}\n{res.link}\n{snippet}"),
            }
        )

    batch.companies.extend(companies.values())
    batch.repositories.extend(repos.values())
    batch.papers.extend(papers.values())
    batch.metrics.extend(
        MetricPoint(entity=ref, metric="hn_points", day=day, value=v)
        for (ref, day), v in points_by_day.items()
    )
    return batch


class HackerNewsProvider:
    name = "hackernews"

    def __init__(
        self, client: HttpClient | None = None, mode: str = "live", cassette_dir: Path | None = None
    ):
        self.client = client or HttpClient(min_interval=MIN_INTERVAL_S, mode=mode, cassette_dir=cassette_dir)

    def enabled(self) -> tuple[bool, str]:
        return True, "public Algolia HN API, no credentials required"

    def _search(
        self, params: dict[str, Any], errors: list[str], *, by_date: bool = False, max_pages: int = MAX_PAGES
    ) -> list[dict[str, Any]]:
        """All hits across up to `max_pages` pages. Failures are appended to `errors`."""
        url = SEARCH_BY_DATE_URL if by_date else SEARCH_URL
        hits: list[dict[str, Any]] = []
        for page in range(max_pages):
            try:
                data = self.client.get_json(
                    url, params={**params, "hitsPerPage": HITS_PER_PAGE, "page": page}
                )
            except (httpx.HTTPError, ValueError, FileNotFoundError) as e:
                errors.append(
                    f"hn search failed ({params.get('tags')}, {params.get('query')!r}, page {page}): {e}"
                )
                break
            hits.extend(data.get("hits") or [])
            if page + 1 >= int(data.get("nbPages") or 0):
                break
        return hits

    @staticmethod
    def _since(days: int) -> int:
        return int((clock.now() - timedelta(days=days)).timestamp())

    def fetch_launches(self, days: int = 14, min_points: int = 5) -> ProviderBatch:
        """Show HN + Launch HN stories from the last `days` days, highest-ranked first."""
        errors: list[str] = []
        hits: list[dict[str, Any]] = []
        for tag in ("launch_hn", "show_hn"):
            hits += self._search(
                {"tags": tag, "numericFilters": f"created_at_i>{self._since(days)},points>={min_points}"},
                errors,
            )
        batch = build_batch(hits)
        batch.errors.extend(errors)
        return batch

    def search_topics(self, queries: list[str], days: int = 60, min_points: int = 20) -> ProviderBatch:
        """Stories matching thesis discovery queries (one page per query)."""
        errors: list[str] = []
        hits: list[dict[str, Any]] = []
        for q in queries:
            hits += self._search(
                {
                    "query": q,
                    "tags": "story",
                    "numericFilters": f"created_at_i>{self._since(days)},points>={min_points}",
                },
                errors,
                max_pages=1,
            )
        batch = build_batch(hits)
        batch.errors.extend(errors)
        return batch

    def mentions(self, term: str, days: int = 90) -> dict[str, Any]:
        """Stories mentioning `term` (exact, case-insensitive, in title/url/text) in the last `days` days.

        `error` is set when the lookup failed, so callers never mistake an outage for zero attention.
        """
        errors: list[str] = []
        needle = term.strip().lower()
        hits = self._search(
            {
                "query": term,
                "tags": "story",
                "typoTolerance": "false",
                "numericFilters": f"created_at_i>{self._since(days)}",
            },
            errors,
            by_date=True,
        )
        stories: list[dict[str, Any]] = []
        seen: set[str] = set()
        for h in hits:
            sid = str(h.get("objectID") or "")
            hay = " ".join(
                [h.get("title") or "", h.get("url") or "", _strip_html(h.get("story_text"))]
            ).lower()
            if not sid or sid in seen or not needle or needle not in hay:
                continue
            seen.add(sid)
            created = _created_at(h)
            stories.append(
                {
                    "title": h.get("title"),
                    "url": h.get("url"),
                    "points": int(h.get("points") or 0),
                    "num_comments": int(h.get("num_comments") or 0),
                    "created_at": created.isoformat() if created else None,
                    "objectID": sid,
                }
            )
        stories.sort(key=lambda s: s["points"], reverse=True)
        return {
            "term": term,
            "days": days,
            "count": len(stories),
            "points_total": sum(s["points"] for s in stories),
            "stories": stories,
            "error": "; ".join(errors) or None,
        }

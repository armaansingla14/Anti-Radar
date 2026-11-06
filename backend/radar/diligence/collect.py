"""Collection: gather a company's public technical artifacts, safely.

Every request goes through :class:`Fetcher`:
* URLs are normalised by ``sanitize_url`` and refused when they point at private, loopback, link-local or
  metadata addresses (checked before the request; the guarded HTTP client re-checks at connect time and on
  every redirect);
* company web pages respect robots.txt and must be HTML; discovered links must stay on the company's own
  registrable domain;
* bodies are size-capped, every artifact's text is capped at ``TEXT_CAP`` characters, and the whole collection
  shares one wall-clock budget — stages that do not fit are skipped and recorded, never silently dropped;
* names taken from fetched text (packages, repositories) are validated and URL-quoted before they are placed in
  a fixed-host API URL.

Nothing fetched is executed, imported or cloned: repository files are read through the GitHub API as text,
and the tree is a list of paths. Every request is recorded in the trace (url, status, ms, bytes, error).
"""

from __future__ import annotations

import html as html_lib
import ipaddress
import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote, urlparse

import feedparser
import httpx
from selectolax.lexbor import LexborHTMLParser
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.diligence.deps import own_package_names, parse_manifests
from radar.diligence.taxonomy import Publisher
from radar.diligence.types import ArtifactDraft, CompanyFacts, parse_dt, parse_version, version_key
from radar.diligence.vocab import BASELINES, baseline_mentions
from radar.llm.guard import detect_injection
from radar.models import Company, MetricSnapshot, Repository, ResearchPaper
from radar.providers.domains import registrable_domain
from radar.providers.http import (
    DeadlineExceeded,
    HttpClient,
    RobotsCache,
    UnsafeURLError,
    _is_public_ip,
    assert_public_host,
    content_hash,
    sanitize_url,
)
from radar.providers.website import ExtractedPage, extract_page, github_org_from_links

log = logging.getLogger(__name__)

TEXT_CAP = 40_000
TREE_CAP = 5_000
MAX_PAGES = 8
MAX_SEEDS = 5  # handoff URLs (a portfolio event's sources) fetched per run
MAX_FILES = 25
MAX_DEP_LOOKUPS = 15
MAX_HN_STORIES = 10
MAX_HN_COMMENTS = 20
MAX_OSV_DETAILS = 10
FILE_BYTES = 400_000
GH_API = "https://api.github.com"
IDENTITY = {"Accept-Encoding": "identity"}  # HttpClient re-wraps decoded bodies; avoid double decoding
ACCEPT_HTML = {"Accept": "text/html,application/xhtml+xml"}
POST_HOSTS = frozenset({"api.osv.dev"})  # the only host we POST to (fixed, never from fetched data)

REPO_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
ORG_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
PYPI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
NPM_NAME = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]{0,100}/)?[a-z0-9][a-z0-9._-]{0,100}$")
OSV_ID = re.compile(r"^[A-Za-z0-9._:-]{3,80}$")


@dataclass
class CollectResult:
    artifacts: list[ArtifactDraft]
    trace: list[dict[str, Any]]
    facts: CompanyFacts
    collected_at: datetime
    partial: bool = False
    errors: list[str] = field(default_factory=list)


def _artifact(key: str, kind: str, publisher: str, url: str, title: str | None, text: str = "",
              data: dict[str, Any] | None = None, status: int | None = 200) -> ArtifactDraft:
    text = (text or "")[:TEXT_CAP]
    data = data or {}
    basis = text or json.dumps(data, sort_keys=True, default=str)
    return ArtifactDraft(key=key[:300], kind=kind, publisher=publisher, url=url[:2000], title=(title or "")[:500] or None,
                         fetched_at=clock.wall_now(), http_status=status, content_hash=content_hash(basis), text=text,
                         data=data, flags=detect_injection(text) if text else [])


# ── safe fetching ────────────────────────────────────────────────────────────


class Fetcher:
    """Guarded, traced, budgeted HTTP for one diligence run.

    ``clients`` lets tests inject stub clients (objects with ``.get(url, **kw) -> httpx.Response``);
    ``resolve`` is the DNS-based public-address check (tests replace it; IP literals are always checked offline).
    """

    def __init__(self, *, budget_s: float = 180.0, github_token: str | None = None,
                 clients: dict[str, Any] | None = None,
                 resolve: Callable[[str], None] = assert_public_host) -> None:
        self.deadline = time.monotonic() + budget_s
        self.token = github_token
        self.resolve = resolve
        self.trace: list[dict[str, Any]] = []
        self._clients: dict[str, Any] = dict(clients or {})
        self._owned: list[HttpClient] = []
        self._robots: dict[str, RobotsCache] = {}
        self.github_limited = False

    # budget
    def remaining(self) -> float:
        return self.deadline - time.monotonic()

    def exhausted(self) -> bool:
        return self.remaining() <= 1.0

    def client(self, name: str):
        if name not in self._clients:
            headers = dict(IDENTITY)
            if name == "github":
                headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
                if self.token:
                    headers["Authorization"] = f"Bearer {self.token}"
            interval = {"web": 0.5, "github": 0.1, "arxiv": 3.0, "hn": 0.3}.get(name, 0.2)
            c = HttpClient(guard_ssrf=True, min_interval=interval, timeout=15.0, max_retries=1, headers=headers)
            self._clients[name] = c
            self._owned.append(c)
        c = self._clients[name]
        if isinstance(c, HttpClient):
            c.set_deadline(max(self.remaining(), 0.5))
        return c

    def guard(self, url: str) -> str:
        """Normalise and refuse non-public targets. Raises UnsafeURLError."""
        url = sanitize_url(url)
        host = (urlparse(url).hostname or "").strip("[]")
        try:
            ipaddress.ip_address(host)
            is_ip = True
        except ValueError:
            is_ip = False
        if is_ip:
            if not _is_public_ip(host):
                raise UnsafeURLError(f"non-public address {host}")
        else:
            self.resolve(url)
        return url

    def _record(self, stage: str, url: str, status: int | None, t0: float, nbytes: int, error: str | None) -> None:
        self.trace.append({"type": "fetch", "stage": stage, "url": url[:500], "status": status,
                           "ms": round((time.perf_counter() - t0) * 1000, 1), "bytes": nbytes, "error": error})

    def get(self, url: str, *, stage: str, client: str = "web", headers: dict[str, str] | None = None,
            params: dict[str, Any] | None = None, max_bytes: int | None = None, robots: bool = False
            ) -> httpx.Response | None:
        t0 = time.perf_counter()
        if self.exhausted():
            self._record(stage, url, None, t0, 0, "skipped: time budget exhausted")
            return None
        if client == "github" and self.github_limited:
            self._record(stage, url, None, t0, 0, "skipped: GitHub rate limit exhausted")
            return None
        try:
            url = self.guard(url)
        except UnsafeURLError as e:
            self._record(stage, url, None, t0, 0, f"refused: unsafe URL ({e})")
            return None
        c = self.client(client)
        if robots:
            base = "{0.scheme}://{0.netloc}".format(urlparse(url))
            rc = self._robots.setdefault(base, RobotsCache(c))
            try:
                allowed = rc.allowed(url)
            except Exception:  # noqa: BLE001 — unreachable robots.txt counts as allowed (standard crawler rule)
                allowed = True
            if not allowed:
                self._record(stage, url, None, t0, 0, "skipped: disallowed by robots.txt")
                return None
        try:
            kw: dict[str, Any] = {"headers": headers}
            if params:
                kw["params"] = params
            if max_bytes:
                kw["max_bytes"] = max_bytes
            r = c.get(url, **kw)
        except UnsafeURLError as e:
            self._record(stage, url, None, t0, 0, f"refused: unsafe URL ({e})")
            return None
        except DeadlineExceeded:
            self._record(stage, url, None, t0, 0, "timeout: budget exhausted")
            return None
        except Exception as e:  # noqa: BLE001 — network failures are recorded, never fatal
            self._record(stage, url, None, t0, 0, f"error: {type(e).__name__}")
            return None
        if client == "github" and r.status_code in (403, 429) and r.headers.get("x-ratelimit-remaining") == "0":
            self.github_limited = True
        err = "truncated at size cap" if r.headers.get("x-radar-truncated") else None
        self._record(stage, url, r.status_code, t0, len(r.content or b""), err)
        return r

    def get_json(self, url: str, **kw: Any) -> Any:
        r = self.get(url, **kw)
        if r is None or r.status_code != 200 or r.headers.get("x-radar-truncated"):
            return None
        try:
            return r.json()
        except ValueError:
            return None

    def post_json(self, url: str, payload: dict[str, Any], *, stage: str) -> Any:
        """POST JSON to an allow-listed API host (OSV). The URL is a constant, never derived from fetched data."""
        t0 = time.perf_counter()
        host = urlparse(url).hostname or ""
        if host not in POST_HOSTS or self.exhausted():
            self._record(stage, url, None, t0, 0, "refused: host not allow-listed" if host not in POST_HOSTS
                         else "skipped: time budget exhausted")
            return None
        c = self.client("api")
        try:
            url = self.guard(url)
            if hasattr(c, "post_json"):  # test stubs
                status, data, nbytes = c.post_json(url, payload)
            else:
                # HttpClient has no request-body API; use its guarded httpx client (same SSRF-checked transport).
                timeout = max(1.0, min(20.0, self.remaining()))
                r = c._client.post(url, json=payload, timeout=timeout)
                status, nbytes = r.status_code, len(r.content or b"")
                data = r.json() if status == 200 and nbytes <= 3 * 1024 * 1024 else None
        except Exception as e:  # noqa: BLE001
            self._record(stage, url, None, t0, 0, f"error: {type(e).__name__}")
            return None
        self._record(stage, url, status, t0, nbytes, None)
        return data

    def close(self) -> None:
        for c in self._owned:
            c.close()


# ── facts ────────────────────────────────────────────────────────────────────


def company_facts(session: Session, company: Company) -> CompanyFacts:
    """Identity + radar metric history for the company's repositories (so replays do not need the DB)."""
    repos = list(session.scalars(select(Repository).where(Repository.company_id == company.id)))
    metrics: dict[str, list[list[Any]]] = {}
    if repos:
        since = clock.today() - timedelta(days=365)
        names = {r.id: r.full_name for r in repos}
        rows = session.execute(
            select(MetricSnapshot.entity_id, MetricSnapshot.metric, MetricSnapshot.day, MetricSnapshot.value)
            .where(MetricSnapshot.entity_id.in_(list(names)), MetricSnapshot.metric.in_(("stars", "contributors")),
                   MetricSnapshot.day >= since)
            .order_by(MetricSnapshot.day)
        ).all()
        for eid, metric, day, value in rows:
            metrics.setdefault(f"{metric}:{names[eid]}", []).append([day.isoformat(), float(value)])
    from radar.models import CompanyPerson, Person

    handles = {h.lower() for row in session.execute(
        select(Person.github_handle, Person.x_handle).join(CompanyPerson, CompanyPerson.person_id == Person.id)
        .where(CompanyPerson.company_id == company.id)).all() for h in row if h}
    if company.x_handle:
        handles.add(company.x_handle.lower())
    return CompanyFacts(name=company.name, slug=company.slug, website=company.website, domain=company.domain,
                        github_org=company.github_org, thesis_tags=list(company.thesis_tags or []),
                        team_handles=sorted(handles), metrics=dict(sorted(metrics.items())))


# ── website ──────────────────────────────────────────────────────────────────

_BLOCK_CLOSE = re.compile(r"(?i)(</(?:p|div|li|h[1-6]|section|article|tr|td|th|header|ul|ol|table|blockquote|pre|dd|dt"
                          r"|figcaption|main|aside|summary)\s*>|<br\s*/?>)")
_SKIP_EXT = re.compile(r"\.(?:pdf|png|jpe?g|gif|svg|webp|mp4|mov|zip|gz|xml|json|ics|css|js|woff2?)$", re.I)
_SKIP_PATH = re.compile(r"(?i)careers|jobs|legal|privacy|terms|cookie|login|signin|sign-in|signup|sign-up|register"
                        r"|cart|checkout|/tag/|/tags/|/author/|/page/\d")
PAGE_KINDS: tuple[tuple[str, re.Pattern[str], int], ...] = tuple(
    (k, re.compile(p, re.I), s) for k, p, s in (
        ("benchmarks", r"bench|performance|\bperf\b|speed|comparison|\bvs\b", 6),
        ("docs", r"\bdocs?\b|documentation|guide|getting[- ]started|quick[- ]?start", 5),
        ("pricing", r"pricing|\bplans\b", 4),
        ("security", r"security|\btrust\b|compliance", 4),
        ("product", r"product|features|platform|solutions?|how[- ]it[- ]works|architecture", 3),
        ("changelog", r"changelog|release[- ]notes|releases", 3),
        ("blog", r"\bblog\b|\bnews\b|updates|announc", 2),
        ("about", r"\babout\b|\bcompany\b", 2),
        ("customers", r"customers|case[- ]stud", 2),
    )
)


def block_text(html: str) -> str:
    """Visible page text with one line per block element (so sentences and headings stay separate)."""
    tree = LexborHTMLParser(_BLOCK_CLOSE.sub(r"\1\n", html))
    tree.strip_tags(["script", "style", "noscript", "nav", "footer", "svg", "template", "iframe", "head", "form"])
    body = tree.body
    raw = body.text(separator=" ") if body else ""
    lines = [re.sub(r"[ \t\r\f\v]+", " ", ln).strip() for ln in raw.split("\n")]
    return "\n".join(ln for ln in lines if ln)[:TEXT_CAP]


def site_of(url: str) -> str | None:
    """The site a URL belongs to: its registrable domain, or its host when the suffix is unknown. None for IPs."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    try:
        ipaddress.ip_address(host.strip("[]"))
        return None
    except ValueError:
        pass
    if not host or "." not in host:
        return None
    return registrable_domain(url) or host.removeprefix("www.")


def same_site(url: str, site: str | None) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return bool(site) and (host == site or host.endswith("." + site))


def pick_pages(home: ExtractedPage, *, limit: int = MAX_PAGES) -> list[tuple[str, str]]:
    """Same-site links most likely to carry technical claims: [(url, page_kind)], best first, ≤2 per kind."""
    site = site_of(home.url)
    home_path = urlparse(home.url).path.rstrip("/")
    scored: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for link in home.links:
        p = urlparse(link.url)
        if not same_site(link.url, site) or p.scheme not in ("http", "https"):
            continue  # off-site, IP literal or non-web link: never followed
        if _SKIP_EXT.search(p.path) or _SKIP_PATH.search(p.path) or p.path.rstrip("/") == home_path:
            continue
        url = f"{p.scheme}://{p.netloc}{p.path or '/'}"
        if url in seen:
            continue
        seen.add(url)
        hay = f"{p.netloc.split('.')[0]} {p.path.replace('/', ' ').replace('-', ' ')} {link.text}".lower()
        best = max(((s, k) for k, pat, s in PAGE_KINDS if pat.search(hay)), default=None)
        if best is None:
            continue
        depth = len([s for s in p.path.split("/") if s])
        scored.append((-best[0], depth, url, best[1]))
    out: list[tuple[str, str]] = []
    per_kind: dict[str, int] = {}
    for _s, _d, url, kind in sorted(scored):
        if per_kind.get(kind, 0) >= 2:
            continue
        per_kind[kind] = per_kind.get(kind, 0) + 1
        out.append((url, kind))
        if len(out) >= limit:
            break
    return out


def _fetch_html(f: Fetcher, url: str, stage: str = "website") -> tuple[str, str, int] | None:
    r = f.get(url, stage=stage, client="web", headers=ACCEPT_HTML, robots=True, max_bytes=2_000_000)
    if r is None or r.status_code != 200:
        return None
    ctype = r.headers.get("content-type", "").lower()
    if ctype and "html" not in ctype:
        return None
    final = str(r.request.url) if r.request is not None else url
    return final, r.text, r.status_code


def collect_website(f: Fetcher, company: Company, facts: CompanyFacts, arts: dict[str, ArtifactDraft]) -> None:
    if not company.website:
        f.trace.append({"type": "stage", "stage": "website", "note": "no website on record"})
        return
    res = _fetch_html(f, company.website)
    if res is None:
        return
    final, html, status = res
    home = extract_page(html, final)
    a = _artifact(f"web:{final}", "web_page", Publisher.COMPANY, final, home.title, block_text(html),
                  {"page_kind": "home"}, status)
    arts[a.key] = a
    if not facts.github_org:
        org = github_org_from_links(home.links, [company.name, company.slug])
        if org and ORG_RE.match(org):
            facts.github_org = org
            a.data["github_org_from_site"] = org
    for url, kind in pick_pages(home):
        res = _fetch_html(f, url)
        if res is None:
            continue
        final, html, status = res
        page = extract_page(html, final)
        a = _artifact(f"web:{final}", "web_page", Publisher.COMPANY, final, page.title, block_text(html),
                      {"page_kind": kind}, status)
        arts.setdefault(a.key, a)


def page_kind_of(url: str) -> str | None:
    """The PAGE_KINDS label a URL's path suggests (None when nothing matches)."""
    p = urlparse(url)
    hay = f"{p.netloc.split('.')[0]} {p.path.replace('/', ' ').replace('-', ' ')}".lower()
    best = max(((s, k) for k, pat, s in PAGE_KINDS if pat.search(hay)), default=None)
    return best[1] if best else None


def company_sites(company: Company, facts: CompanyFacts) -> list[str]:
    """The registrable domains that count as the company's own (website and recorded domain)."""
    sites = [site_of(company.website or ""), site_of(f"https://{facts.domain}") if facts.domain else None]
    return sorted({s for s in sites if s})


def collect_seeds(f: Fetcher, company: Company, facts: CompanyFacts, arts: dict[str, ArtifactDraft],
                  seed_urls: list[str]) -> None:
    """Pages a portfolio event pointed at (the diligence handoff), fetched as company web pages.

    Only URLs on the company's own domain are fetched — with the same SSRF guard, robots.txt check, HTML-only rule
    and size caps as the website crawl; a redirect off the company's domain is dropped. The crawl follows links
    from the homepage only, so a blog post the homepage does not link (e.g. Modal's "NVIDIA Vera CPUs double
    throughput for Modal Sandboxes") is reachable only this way."""
    sites = company_sites(company, facts)
    for raw in list(dict.fromkeys(u for u in seed_urls if u))[:MAX_SEEDS]:
        t0 = time.perf_counter()
        try:
            url = sanitize_url(raw)
        except UnsafeURLError as e:
            f._record("seeds", str(raw), None, t0, 0, f"refused: unsafe URL ({e})")
            continue
        if not any(same_site(url, site) for site in sites):
            f._record("seeds", url, None, t0, 0, "refused: not on the company's own domain")
            continue
        res = _fetch_html(f, url, stage="seeds")
        if res is None:
            continue
        final, html, status = res
        if not any(same_site(final, site) for site in sites):
            f._record("seeds", final, None, t0, 0, "refused: redirected off the company's own domain")
            continue
        key = f"web:{final}"
        if key in arts:
            arts[key].data["seed"] = True
            continue
        page = extract_page(html, final)
        arts[key] = _artifact(key, "web_page", Publisher.COMPANY, final, page.title, block_text(html),
                              {"page_kind": page_kind_of(final) or "seed", "seed": True}, status)


# ── GitHub ───────────────────────────────────────────────────────────────────

FILE_RULES: tuple[tuple[re.Pattern[str], str, int], ...] = tuple(
    (re.compile(p, re.I), group, cap) for p, group, cap in (
        (r"^(?:[^/]+/)?pyproject\.toml$", "manifest", 3),
        (r"^(?:[^/]+/)?requirements[\w.-]*\.(?:txt|in)$", "manifest", 4),
        (r"^setup\.py$", "manifest", 1),
        (r"^setup\.cfg$", "manifest", 1),
        (r"^(?:[^/]+/)?package\.json$", "manifest", 3),
        (r"^(?:[^/]+/)?Cargo\.toml$", "manifest", 2),
        (r"^(?:[^/]+/)?go\.mod$", "manifest", 2),
        (r"^(?:[^/]+/){0,2}Dockerfile[\w.-]*$", "container", 3),
        (r"^(?:[^/]+/)?(?:docker-)?compose[\w.-]*\.ya?ml$", "container", 2),
        (r"^(?:[^/]+/)?\.?env[\w.-]*\.(?:example|sample|template)$", "config", 2),
        (r"^(?:\.github/|docs/)?SECURITY\.md$", "policy", 1),
        (r"^LICEN[CS]E(?:\.\w+)?$", "policy", 1),
        (r"^\.github/workflows/[^/]+\.ya?ml$", "ci", 4),
        (r"^docs?/(?:[^/]+/)?[^/]*bench[^/]*\.md$", "doc", 3),
    )
)
_VENDORED = re.compile(r"(^|/)(node_modules|vendor|third_party|3rdparty|\.venv|site-packages|dist|build)/")


def select_files(paths: list[str], *, limit: int = MAX_FILES) -> list[str]:
    """Manifests, container/CI configs, policies and benchmark docs worth reading (paths only; never executed)."""
    out: list[str] = []
    used: dict[int, int] = {}
    for i, (pat, _group, cap) in enumerate(FILE_RULES):
        for p in sorted(paths, key=lambda x: (x.count("/"), x)):
            if len(out) >= limit:
                return out
            if used.get(i, 0) >= cap or _VENDORED.search(p) or p in out:
                continue
            if pat.search(p):
                out.append(p)
                used[i] = used.get(i, 0) + 1
    return out


def _gh(f: Fetcher, path: str, *, stage: str = "github", accept: str | None = None,
        params: dict[str, Any] | None = None, max_bytes: int | None = None) -> httpx.Response | None:
    return f.get(GH_API + path, stage=stage, client="github", headers={"Accept": accept} if accept else None,
                 params=params, max_bytes=max_bytes)


def _gh_json(f: Fetcher, path: str, **kw: Any) -> Any:
    r = _gh(f, path, **kw)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def _gh_stats(f: Fetcher, path: str) -> Any:
    """Stats endpoints answer 202 while GitHub computes them: one short retry, then give up (recorded)."""
    r = _gh(f, path)
    if r is not None and r.status_code == 202 and f.remaining() > 10:
        time.sleep(2.0)
        r = _gh(f, path)
    if r is None or r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def pick_repos(f: Fetcher, session: Session, company: Company, facts: CompanyFacts, limit: int) -> list[str]:
    cands: dict[str, int] = {}
    for r in session.scalars(select(Repository).where(Repository.company_id == company.id)):
        if REPO_RE.match(r.full_name) and not (r.attrs or {}).get("fork"):
            cands[r.full_name] = max(cands.get(r.full_name, 0), r.stars or 0)
    org = facts.github_org
    if org and ORG_RE.match(org):
        listing = _gh_json(f, f"/orgs/{org}/repos", params={"per_page": 100, "type": "public", "sort": "pushed"})
        if listing is None:
            listing = _gh_json(f, f"/users/{org}/repos", params={"per_page": 100, "sort": "pushed"})
        for r in listing or []:
            name = str(r.get("full_name") or "")
            if REPO_RE.match(name) and not r.get("fork"):
                cands[name] = max(cands.get(name, 0), int(r.get("stargazers_count") or 0))
    return [n for n, _s in sorted(cands.items(), key=lambda kv: (-kv[1], kv[0].lower()))][:limit]


def collect_repo(f: Fetcher, fn: str, arts: dict[str, ArtifactDraft], *, as_of: datetime) -> None:
    if not REPO_RE.match(fn):
        return
    meta = _gh_json(f, f"/repos/{fn}")
    if not isinstance(meta, dict):
        return
    html = meta.get("html_url") or f"https://github.com/{fn}"
    branch = str(meta.get("default_branch") or "main")
    repo_data = {
        "full_name": fn, "html_url": html, "description": meta.get("description"), "homepage": meta.get("homepage"),
        "license_spdx": (meta.get("license") or {}).get("spdx_id"), "archived": bool(meta.get("archived")),
        "fork": bool(meta.get("fork")), "default_branch": branch, "created_at": meta.get("created_at"),
        "pushed_at": meta.get("pushed_at"), "stars": meta.get("stargazers_count"), "forks": meta.get("forks_count"),
        "open_issues": meta.get("open_issues_count"), "topics": sorted(meta.get("topics") or []),
        "language": meta.get("language"),
    }
    arts[f"repo:{fn}"] = _artifact(f"repo:{fn}", "repo", Publisher.CODE, html, f"GitHub {fn}",
                                   meta.get("description") or "", repo_data)

    r = _gh(f, f"/repos/{fn}/readme", accept="application/vnd.github.raw", max_bytes=FILE_BYTES)
    if r is not None and r.status_code == 200:
        # README prose is written by the company: a company statement, never code evidence.
        arts[f"readme:{fn}"] = _artifact(f"readme:{fn}", "readme", Publisher.COMPANY, f"{html}#readme",
                                         f"{fn} README", r.text, {"full_name": fn})
    langs = _gh_json(f, f"/repos/{fn}/languages")
    if isinstance(langs, dict):
        arts[f"languages:{fn}"] = _artifact(f"languages:{fn}", "languages", Publisher.CODE, html, f"{fn} languages",
                                            data={"full_name": fn, "bytes": {k: v for k, v in sorted(langs.items())
                                                                             if isinstance(v, int)}})
    tree = _gh_json(f, f"/repos/{fn}/git/trees/{quote(branch, safe='')}", params={"recursive": "1"})
    paths: list[str] = []
    if isinstance(tree, dict):
        blobs = [str(t.get("path")) for t in tree.get("tree") or [] if t.get("type") == "blob" and t.get("path")]
        paths = sorted(blobs)[:TREE_CAP]
        arts[f"tree:{fn}"] = _artifact(f"tree:{fn}", "tree", Publisher.CODE, f"{html}/tree/{branch}",
                                       f"{fn} file tree", data={"full_name": fn, "paths": paths, "total": len(blobs),
                                                                "truncated": bool(tree.get("truncated")) or len(blobs) > TREE_CAP})
    for path in select_files(paths):
        r = _gh(f, f"/repos/{fn}/contents/{quote(path)}", accept="application/vnd.github.raw",
                params={"ref": branch}, max_bytes=FILE_BYTES)
        if r is None or r.status_code != 200:
            continue
        is_doc = path.lower().endswith(".md") and path.lower().startswith(("doc/", "docs/"))
        kind, pub = ("doc", Publisher.COMPANY) if is_doc else ("file", Publisher.CODE)
        key = f"file:{fn}:{path}"
        arts[key] = _artifact(key, kind, pub, f"{html}/blob/{branch}/{path}", f"{fn}/{path}", r.text,
                              {"path": path, "full_name": fn})

    rel = _gh_json(f, f"/repos/{fn}/releases", params={"per_page": 50})
    if isinstance(rel, list):
        releases = [{"tag": x.get("tag_name"), "name": (x.get("name") or "")[:120], "published_at": x.get("published_at"),
                     "prerelease": bool(x.get("prerelease")), "draft": bool(x.get("draft")), "html_url": x.get("html_url")}
                    for x in rel if isinstance(x, dict)]
        arts[f"releases:{fn}"] = _artifact(f"releases:{fn}", "releases", Publisher.CODE, f"{html}/releases",
                                           f"{fn} releases", data={"full_name": fn, "releases": releases})

    con = _gh_json(f, f"/repos/{fn}/contributors", params={"per_page": 100})
    if isinstance(con, list):
        people = [{"login": c.get("login"), "contributions": int(c.get("contributions") or 0), "type": c.get("type")}
                  for c in con if isinstance(c, dict) and c.get("login")]
        weekly = []
        stats = _gh_stats(f, f"/repos/{fn}/stats/contributors")
        cutoff = (as_of - timedelta(days=182)).timestamp()
        for s in stats or []:
            login = (s.get("author") or {}).get("login")
            if login:
                weekly.append({"login": login, "total": s.get("total"),
                               "weeks": [w for w in s.get("weeks") or [] if int(w.get("w", 0)) >= cutoff]})
        arts[f"contributors:{fn}"] = _artifact(
            f"contributors:{fn}", "contributors", Publisher.CODE, f"{html}/graphs/contributors", f"{fn} contributors",
            data={"full_name": fn, "contributors": people, "capped": len(people) >= 100,
                  "weekly": sorted(weekly, key=lambda w: w["login"])})
    act = _gh_stats(f, f"/repos/{fn}/stats/commit_activity")
    if isinstance(act, list):
        arts[f"commits:{fn}"] = _artifact(f"commits:{fn}", "commits", Publisher.CODE, f"{html}/graphs/commit-activity",
                                          f"{fn} commit activity", data={"full_name": fn, "weeks": [
                                              {"week": w.get("week"), "total": w.get("total")} for w in act]})
    issues = _gh_json(f, f"/repos/{fn}/issues", params={"state": "closed", "sort": "updated", "direction": "desc",
                                                         "per_page": 100})
    if isinstance(issues, list):
        closed = [{"number": i.get("number"), "created_at": i.get("created_at"), "closed_at": i.get("closed_at"),
                   "comments": i.get("comments")} for i in issues
                  if isinstance(i, dict) and "pull_request" not in i and i.get("closed_at")]
        closed.sort(key=lambda i: str(i["closed_at"]), reverse=True)
        arts[f"issues:{fn}"] = _artifact(f"issues:{fn}", "issues", Publisher.CODE, f"{html}/issues?q=is%3Aclosed",
                                         f"{fn} recently closed issues", data={"full_name": fn, "issues": closed[:50]})
    if f.token:
        counts = {}
        for days in (30, 90):
            since = (as_of - timedelta(days=days)).date().isoformat()
            res = _gh_json(f, "/search/issues", stage="github_search",
                           params={"q": f"repo:{fn} is:pr is:merged merged:>={since}", "per_page": 1})
            counts[f"merged_{days}d"] = res.get("total_count") if isinstance(res, dict) else None
        if counts.get("merged_30d") is not None:
            arts[f"prs:{fn}"] = _artifact(f"prs:{fn}", "prs", Publisher.CODE, f"{html}/pulls?q=is%3Amerged",
                                          f"{fn} merged pull requests", data={"full_name": fn, **counts})
    adv = _gh_json(f, f"/repos/{fn}/security-advisories", params={"state": "published", "per_page": 30})
    if isinstance(adv, list):
        items = [{"ghsa_id": a.get("ghsa_id"), "cve_id": a.get("cve_id"), "severity": a.get("severity"),
                  "summary": (a.get("summary") or "")[:300], "published_at": a.get("published_at"),
                  "html_url": a.get("html_url")} for a in adv if isinstance(a, dict)]
        arts[f"advisories:{fn}"] = _artifact(f"advisories:{fn}", "advisories", Publisher.CODE,
                                             f"{html}/security/advisories", f"{fn} security advisories",
                                             data={"full_name": fn, "advisories": items})


def collect_github(f: Fetcher, session: Session, company: Company, facts: CompanyFacts,
                   arts: dict[str, ArtifactDraft], as_of: datetime) -> None:
    limit = 3 if f.token else 1  # anonymous GitHub allows 60 requests/hour: one repository only
    for fn in pick_repos(f, session, company, facts, limit):
        if f.exhausted() or f.github_limited:
            break
        collect_repo(f, fn, arts, as_of=as_of)


# ── registries ───────────────────────────────────────────────────────────────


def _pypi(f: Fetcher, name: str, roles: list[str]) -> ArtifactDraft | None:
    if not PYPI_NAME.match(name):
        return None
    r = f.get(f"https://pypi.org/rss/project/{quote(name)}/releases.xml", stage="registry", client="api")
    if r is None or r.status_code != 200:
        return None
    feed = feedparser.parse(r.text)
    versions = []
    for e in feed.entries[:60]:
        v = str(e.get("title") or "").strip()
        p = parse_version(v)
        if p is None:
            continue
        d = parse_dt(e.get("published"))
        versions.append({"version": v, "date": d.isoformat() if d else None, "prerelease": p[3]})
    versions.sort(key=lambda x: version_key(x["version"]))
    stable = [v for v in versions if not v["prerelease"]] or versions
    latest = stable[-1] if stable else None
    data: dict[str, Any] = {"ecosystem": "pypi", "name": name, "roles": sorted(roles),
                            "latest_version": latest["version"] if latest else None,
                            "latest_release_at": latest["date"] if latest else None, "versions": versions,
                            "deprecated": None, "summary": None, "project_urls": {}}
    if latest:
        info = f.get_json(f"https://pypi.org/pypi/{quote(name)}/{quote(latest['version'])}/json", stage="registry",
                          client="api")
        if isinstance(info, dict):
            i = info.get("info") or {}
            data["summary"] = (i.get("summary") or "")[:300]
            data["project_urls"] = {str(k)[:60]: str(v)[:300] for k, v in sorted((i.get("project_urls") or {}).items())}
            if any("7 - Inactive" in c for c in i.get("classifiers") or []):
                data["deprecated"] = "Development Status :: 7 - Inactive"
    if "own" in roles:
        dl = f.get_json(f"https://pypistats.org/api/packages/{quote(name.lower())}/recent", stage="registry",
                        client="api")
        if isinstance(dl, dict) and isinstance(dl.get("data"), dict):
            data["downloads"] = {k: dl["data"].get(k) for k in ("last_day", "last_week", "last_month")}
            data["downloads_source"] = "pypistats.org (recent, without mirrors)"
    return _artifact(f"pkg:pypi:{name.lower()}", "package", Publisher.REGISTRY, f"https://pypi.org/project/{name}/",
                     f"PyPI {name}", data=data)


def _npm(f: Fetcher, name: str, roles: list[str]) -> ArtifactDraft | None:
    if not NPM_NAME.match(name):
        return None
    enc = quote(name, safe="@")
    latest = f.get_json(f"https://registry.npmjs.org/{enc}/latest", stage="registry", client="api")
    if not isinstance(latest, dict):
        return None
    data: dict[str, Any] = {"ecosystem": "npm", "name": name, "roles": sorted(roles),
                            "latest_version": latest.get("version"), "latest_release_at": None, "versions": [],
                            "deprecated": latest.get("deprecated"), "summary": (latest.get("description") or "")[:300]}
    full = f.get_json(f"https://registry.npmjs.org/{enc}", stage="registry", client="api", max_bytes=3_000_000)
    if isinstance(full, dict):
        times = full.get("time") or {}
        data["latest_release_at"] = times.get(str(latest.get("version")))
        data["versions"] = sorted(({"version": v, "date": t, "prerelease": bool((parse_version(v) or (0, 0, 0, True))[3])}
                                   for v, t in times.items() if v not in ("created", "modified")),
                                  key=lambda x: version_key(x["version"]))[-60:]
    if "own" in roles:
        dls = {}
        for period, field_name in (("last-day", "last_day"), ("last-week", "last_week"), ("last-month", "last_month")):
            d = f.get_json(f"https://api.npmjs.org/downloads/point/{period}/{enc}", stage="registry", client="api")
            dls[field_name] = d.get("downloads") if isinstance(d, dict) else None
        data["downloads"] = dls
        data["downloads_source"] = "api.npmjs.org"
    return _artifact(f"pkg:npm:{name.lower()}", "package", Publisher.REGISTRY, f"https://www.npmjs.com/package/{name}",
                     f"npm {name}", data=data)


def collect_registries(f: Fetcher, arts: dict[str, ArtifactDraft]) -> None:
    current = list(arts.values())
    roles: dict[tuple[str, str], set[str]] = {}
    for eco, name in own_package_names(current)[:4]:
        roles.setdefault((eco, name), set()).add("own")
    for a in current:
        if a.publisher == Publisher.COMPANY and a.text:
            for base, _ver, _span in baseline_mentions(a.text):
                pkg = BASELINES[base][1]
                if pkg:
                    roles.setdefault(("pypi", pkg), set()).add("baseline")
    deps = [d for d in parse_manifests(current)["dependencies"]
            if not d["dev"] and d["kind"] == "registry" and d["ecosystem"] in ("pypi", "npm")]
    for d in deps:
        key = (d["ecosystem"], d["name"])
        if key not in roles and sum(1 for r in roles.values() if "dependency" in r) >= MAX_DEP_LOOKUPS:
            continue
        roles.setdefault(key, set()).add("dependency")
    for (eco, name), rs in sorted(roles.items(), key=lambda kv: (0 if "own" in kv[1] else 1 if "baseline" in kv[1]
                                                                 else 2, kv[0])):
        if f.exhausted():
            break
        a = _pypi(f, name, sorted(rs)) if eco == "pypi" else _npm(f, name, sorted(rs))
        if a is not None:
            arts.setdefault(a.key, a)


OSV_ECOSYSTEM = {"pypi": "PyPI", "npm": "npm", "cargo": "crates.io", "go": "Go"}


def collect_osv(f: Fetcher, arts: dict[str, ArtifactDraft]) -> None:
    """OSV only for exactly pinned dependencies; unpinned ones are listed as not checked."""
    deps = parse_manifests(list(arts.values()))["dependencies"]
    pinned: dict[tuple[str, str, str], None] = {}
    unpinned: set[str] = set()
    for d in deps:
        if d["ecosystem"] not in OSV_ECOSYSTEM or d["kind"] != "registry":
            continue
        if d["pinned_version"]:
            pinned[(d["ecosystem"], d["name"], d["pinned_version"])] = None
        else:
            unpinned.add(d["name"])
    queries = sorted(pinned)[:100]
    if not queries:
        if unpinned:
            arts["osv:batch"] = _artifact("osv:batch", "osv", Publisher.REGISTRY, "https://osv.dev/",
                                          "OSV.dev batch query", data={"queries": [], "results": [],
                                                                      "not_checked_unpinned": sorted(unpinned)})
        return
    payload = {"queries": [{"package": {"name": n, "ecosystem": OSV_ECOSYSTEM[e]}, "version": v} for e, n, v in queries]}
    res = f.post_json("https://api.osv.dev/v1/querybatch", payload, stage="osv")
    if not isinstance(res, dict):
        return
    results = []
    ids: list[str] = []
    for (eco, name, ver), r in zip(queries, res.get("results") or []):
        vulns = [{"id": v.get("id")} for v in (r or {}).get("vulns") or [] if OSV_ID.match(str(v.get("id") or ""))]
        ids += [v["id"] for v in vulns]
        results.append({"ecosystem": eco, "name": name, "version": ver, "vulns": vulns})
    details: dict[str, dict] = {}
    for vid in sorted(set(ids))[:MAX_OSV_DETAILS]:
        d = f.get_json(f"https://api.osv.dev/v1/vulns/{quote(vid)}", stage="osv", client="api")
        if isinstance(d, dict):
            details[vid] = {"aliases": sorted(d.get("aliases") or [])[:10], "summary": (d.get("summary") or "")[:300],
                            "database_specific": {"severity": (d.get("database_specific") or {}).get("severity")}}
    for r in results:
        for v in r["vulns"]:
            v.update(details.get(v["id"], {}))
    arts["osv:batch"] = _artifact("osv:batch", "osv", Publisher.REGISTRY, "https://osv.dev/", "OSV.dev batch query",
                                  data={"queries": [{"ecosystem": OSV_ECOSYSTEM[e], "name": n, "version": v}
                                                    for e, n, v in queries],
                                        "results": results, "not_checked_unpinned": sorted(unpinned)})


# ── third parties ────────────────────────────────────────────────────────────

_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str | None) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG.sub(" ", s or ""))).strip()


def _brand(facts: CompanyFacts) -> str:
    """The distinctive part of the company name ("Unsloth AI" → "Unsloth")."""
    words = [w for w in re.split(r"[\s._-]+", facts.name or "") if w]
    core = [w for w in words if w.lower() not in _NAME_SUFFIXES]
    return " ".join(core or words)


_NAME_SUFFIXES = {"ai", "inc", "labs", "lab", "llc", "ltd", "corp", "co", "hq", "io", "technologies", "systems"}


def _names(arts: dict[str, ArtifactDraft], facts: CompanyFacts) -> list[str]:
    """Search terms that identify THIS company. Repository and package names are used only when they contain the
    company's own name: generic names ("notebooks", "docs", "studio") match unrelated threads."""
    brand = _brand(facts)
    names = [facts.name, brand]
    names += [a.data.get("full_name", "").split("/")[-1] for a in arts.values() if a.kind == "repo"]
    names += [str(a.data.get("name")) for a in arts.values() if a.kind == "package" and "own" in (a.data.get("roles") or [])]
    out: list[str] = []
    for n in names:
        n = (n or "").strip()
        if len(n) < 4 or n.lower() in {x.lower() for x in out}:
            continue
        if n not in (facts.name, brand) and not _mentions(n, brand):
            continue
        out.append(n)
    return out[:3]


def _own_post(h: dict, title: str, facts: CompanyFacts, names: list[str]) -> bool:
    """A company statement: posted by a known team member, or a Show/Launch HN that names the company (or links its
    site). When authorship is unknown we err towards "company": calling a company's own launch post independent
    would inflate the support for its claims."""
    author = str(h.get("author") or "").lower()
    if author and author in set(facts.team_handles):
        return True
    if not re.match(r"^(Show|Launch) HN\b", title):
        return False
    host = urlparse(str(h.get("url") or "")).hostname or ""
    own_site = bool(facts.domain) and (host == facts.domain or host.endswith("." + str(facts.domain)))
    return own_site or _mentions(title, _brand(facts)) or any(_mentions(title, n) for n in names)


def _mentions(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9]){re.escape(name)}(?![A-Za-z0-9])", text or "", re.I) is not None


def collect_hn(f: Fetcher, arts: dict[str, ArtifactDraft], facts: CompanyFacts) -> None:
    names = _names(arts, facts)
    stories: dict[str, dict] = {}
    for q in names:
        data = f.get_json("https://hn.algolia.com/api/v1/search", stage="hackernews", client="hn",
                          params={"query": q, "tags": "story", "hitsPerPage": 8})
        for h in (data or {}).get("hits") or []:
            oid = str(h.get("objectID") or "")
            brand = _brand(facts)
            if oid.isdigit() and (_mentions(h.get("title") or "", brand) or _mentions(h.get("url") or "", brand)):
                stories.setdefault(oid, h)
    top = sorted(stories.values(), key=lambda h: (-(h.get("points") or 0), str(h.get("objectID"))))[:MAX_HN_STORIES]
    for h in top:
        oid = str(h["objectID"])
        title = h.get("title") or ""
        own = _own_post(h, title, facts, names)
        a = _artifact(f"hn:{oid}", "hn_story", Publisher.COMPANY if own else Publisher.THIRD_PARTY,
                      f"https://news.ycombinator.com/item?id={oid}", title,
                      "\n".join(x for x in (title, _strip_html(h.get("story_text"))[:3000]) if x),
                      {"points": h.get("points"), "num_comments": h.get("num_comments"), "created_at": h.get("created_at"),
                       "author": h.get("author"), "url": h.get("url")})
        arts[a.key] = a
    n_comments = 0
    for h in top[:3]:
        if n_comments >= MAX_HN_COMMENTS or f.exhausted():
            break
        oid = str(h["objectID"])
        data = f.get_json("https://hn.algolia.com/api/v1/search", stage="hackernews", client="hn",
                          params={"tags": f"comment,story_{oid}", "hitsPerPage": 10})
        story_author = h.get("author")
        story_is_company = arts[f"hn:{oid}"].publisher == Publisher.COMPANY
        for c in (data or {}).get("hits") or []:
            cid = str(c.get("objectID") or "")
            text = _strip_html(c.get("comment_text"))[:3000]
            if not cid.isdigit() or not text:
                continue
            by_company = (story_is_company and c.get("author") == story_author) or \
                str(c.get("author") or "").lower() in set(facts.team_handles)
            a = _artifact(f"hn-comment:{cid}", "hn_comment", Publisher.COMPANY if by_company else Publisher.THIRD_PARTY,
                          f"https://news.ycombinator.com/item?id={cid}", f"Comment on: {h.get('title') or oid}"[:300],
                          text, {"story_id": oid, "author": c.get("author"), "created_at": c.get("created_at")})
            arts[a.key] = a
            n_comments += 1
            if n_comments >= MAX_HN_COMMENTS:
                break


def collect_papers(session: Session, company: Company, arts: dict[str, ArtifactDraft], facts: CompanyFacts) -> None:
    """Papers already in the radar that link the company's code (no network)."""
    pats = [f"%github.com/{a.data.get('full_name')}%" for a in arts.values() if a.kind == "repo"]
    if facts.github_org:
        pats.append(f"%github.com/{facts.github_org}/%")
    conds = [ResearchPaper.company_id == company.id]
    conds += [func.array_to_string(ResearchPaper.code_urls, " ").ilike(p) for p in pats]
    rows = session.scalars(select(ResearchPaper).where(or_(*conds)).order_by(ResearchPaper.published_at.desc().nullslast(),
                                                                              ResearchPaper.arxiv_id).limit(10))
    for p in rows:
        # A paper the radar attributes to the company is the company's own work, not independent evidence.
        pub = Publisher.COMPANY if p.company_id == company.id else Publisher.THIRD_PARTY
        a = _artifact(f"paper:{p.arxiv_id}", "paper", pub, p.url or f"https://arxiv.org/abs/{p.arxiv_id}",
                      " ".join((p.title or "").split()), f"{' '.join((p.title or '').split())}\n{p.abstract or ''}",
                      {"arxiv_id": p.arxiv_id, "published_at": p.published_at.isoformat() if p.published_at else None,
                       "code_urls": list(p.code_urls or [])[:5]})
        a.fetched_at = p.updated_at or p.first_seen_at
        arts[a.key] = a


# ── orchestration ────────────────────────────────────────────────────────────


def collect(session: Session, company: Company, *, budget_s: float = 180.0, fetcher: Fetcher | None = None,
            seed_urls: list[str] | None = None) -> CollectResult:
    """Collect public artifacts for ``company`` within ``budget_s`` seconds. Partial collection is recorded.

    ``seed_urls`` (the diligence handoff from a portfolio event) are fetched right after the website crawl, on the
    company's own domain only (:func:`collect_seeds`)."""
    from radar.settings import get_settings

    started = clock.wall_now()
    own = fetcher is None
    f = fetcher or Fetcher(budget_s=budget_s, github_token=get_settings().resolved_github_token())
    facts = company_facts(session, company)
    arts: dict[str, ArtifactDraft] = {}
    partial = False
    errors: list[str] = []
    stages: list[tuple[str, Callable[[], None]]] = [
        ("website", lambda: collect_website(f, company, facts, arts)),
        *([("seeds", lambda: collect_seeds(f, company, facts, arts, list(seed_urls or [])))] if seed_urls else []),
        ("github", lambda: collect_github(f, session, company, facts, arts, started)),
        ("registries", lambda: collect_registries(f, arts)),
        ("osv", lambda: collect_osv(f, arts)),
        ("hackernews", lambda: collect_hn(f, arts, facts)),
        ("papers", lambda: collect_papers(session, company, arts, facts)),
    ]
    try:
        for name, fn in stages:
            if f.exhausted() and name != "papers":
                f.trace.append({"type": "stage", "stage": name, "skipped": "time budget exhausted"})
                partial = True
                continue
            t0 = time.perf_counter()
            before = len(arts)
            try:
                fn()
            except Exception as e:  # noqa: BLE001 — one failing stage must not lose the others
                log.warning("diligence collection stage %s failed: %s", name, e)
                errors.append(f"{name}: {type(e).__name__}")
                partial = True
            f.trace.append({"type": "stage", "stage": name, "ms": round((time.perf_counter() - t0) * 1000, 1),
                            "artifacts": len(arts) - before})
    finally:
        if own:
            f.close()
    if any(t.get("error", "") and str(t.get("error")).startswith(("timeout", "skipped: time")) for t in f.trace):
        partial = True
    return CollectResult(artifacts=sorted(arts.values(), key=lambda a: a.key), trace=f.trace, facts=facts,
                         collected_at=started, partial=partial, errors=errors)

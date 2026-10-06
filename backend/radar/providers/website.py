"""Company website intelligence.

Crawls a handful of pages on a company's own site (homepage, about, team, careers,
blog, research…) politely and safely, and turns them into *facts*:

* a CompanyRecord (name / description / website / GitHub org / X handle as linked by the site),
* page sources with content hashes (the pipeline diffs hashes for `website_changed`),
* JobRecords — only from public ATS JSON boards or clearly structured job links,
* signals: `hiring_active` (≥1 open technical role) and `formation_language`.

All fetched content is untrusted data: it is parsed, never executed, and length-capped.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser

from radar import clock
from radar.providers import ats as ats_mod
from radar.providers.ats import IDENTITY_ENCODING
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    JobRecord,
    MetricPoint,
    NormalizedSignal,
    ProviderBatch,
    domain_ref,
    gh_org_ref,
)
from radar.providers.domains import GITHUB_RESERVED, company_domain, registrable_domain
from radar.providers.formation import (
    GENERIC_PHRASES,
    PERSONAL_PHRASES,
    find_formation_matches,
    phrase_context,
)
from radar.providers.http import (
    HttpClient,
    RobotsCache,
    UnsafeURLError,
    assert_public_host,
    content_hash,
    sanitize_url,
)
from radar.providers.roles import classify_role, is_technical
from radar.thesis.loader import Thesis, get_thesis

PAGE_KIND_SEGMENTS: dict[str, tuple[str, ...]] = {
    "careers": ("careers", "career", "jobs", "join", "join-us", "work-with-us", "hiring", "open-roles", "openings",
                "positions"),
    "about": ("about", "about-us", "company", "mission", "our-story", "who-we-are"),
    "team": ("team", "our-team", "people", "leadership", "founders"),
    "research": ("research", "publications", "papers"),
    "blog": ("blog", "posts", "writing"),
    "news": ("news", "press", "newsroom", "media", "announcements"),
    "docs": ("docs", "documentation", "developers"),
    "changelog": ("changelog", "releases", "release-notes"),
}
PAGE_KIND_ANCHORS: dict[str, tuple[str, ...]] = {
    "careers": ("careers", "career", "jobs", "join us", "join the team", "we're hiring", "open roles", "work with us"),
    "about": ("about", "about us", "company", "our mission", "our story"),
    "team": ("team", "our team", "people", "leadership"),
    "research": ("research", "publications", "papers"),
    "blog": ("blog", "engineering blog"),
    "news": ("news", "press", "newsroom"),
    "docs": ("docs", "documentation", "developers"),
    "changelog": ("changelog", "release notes"),
}
FETCH_PRIORITY = ("careers", "about", "team", "research", "blog", "news", "docs", "changelog")
FORMATION_PAGE_KINDS = ("home", "about")
# Phrases that on a company site do not indicate formation ("we're hiring", "co-founder & CEO", …).
WEBSITE_FORMATION_EXCLUDE = GENERIC_PHRASES | PERSONAL_PHRASES

_LOCALE_SEG = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$")
_SKIP_EXT = re.compile(r"\.(?:pdf|png|jpe?g|gif|svg|webp|mp4|mov|zip|gz|xml|json|ics|css|js)$", re.IGNORECASE)
_JOB_PATH = re.compile(
    r"^/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:careers|jobs|positions|openings|open-roles|roles)/(?:[^/]+/)?[^/]+/?$", re.IGNORECASE
)
_NON_JOB_SLUGS = {"apply", "benefits", "life", "culture", "faq", "faqs", "teams", "departments", "search", "all",
                  "students", "university", "values", "perks", "locations", "privacy", "page"}
_JOB_TITLE_WORD = re.compile(
    r"\b(?:engineer|engineering|scientist|researcher|developer|technician|architect|manager|lead|director|head of|"
    r"designer|analyst|specialist|operator|machinist|intern|internship|fellow|associate|recruiter|counsel|"
    r"coordinator|planner|buyer|accountant|administrator|officer|president|executive|representative|welder|"
    r"assembler|inspector|mechanic|electrician|pilot)s?\b",
    re.IGNORECASE,
)
_BLOCK_TITLES = re.compile(
    r"security checkpoint|just a moment|attention required|access denied|are you a robot|captcha|ddos protection",
    re.IGNORECASE,
)
_GENERIC_TITLE_SEGMENTS = {"home", "homepage", "welcome", "official site", "official website"}
_GH_LOGIN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_X_HANDLE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_X_HOSTS = {"x.com", "www.x.com", "twitter.com", "www.twitter.com", "mobile.twitter.com"}
_X_RESERVED = {"home", "intent", "share", "search", "i", "hashtag", "explore", "settings", "login", "signup",
               "privacy", "tos", "about", "notifications", "messages", "compose", "jobs"}
MAX_TEXT = 200_000


@dataclass
class Link:
    url: str
    text: str
    in_chrome: bool  # inside <nav>/<header>/<footer> — where sites put their identity links


@dataclass
class ExtractedPage:
    url: str
    title: str | None
    description: str | None
    site_name: str | None
    text: str
    links: list[Link] = field(default_factory=list)
    html: str = ""


def _squash(s: str | None, limit: int | None = None) -> str | None:
    if not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    if limit:
        s = s[:limit]
    return s or None


def extract_page(html: str, url: str) -> ExtractedPage:
    """Parse untrusted HTML into title, meta description, og:site_name, visible text and links."""
    tree = LexborHTMLParser(html)
    title_node = tree.css_first("title")
    title = _squash(title_node.text() if title_node else None, 300)
    meta: dict[str, str] = {}
    for m in tree.css("meta"):
        key = (m.attributes.get("name") or m.attributes.get("property") or "").strip().lower()
        content = m.attributes.get("content")
        if key and content and key not in meta:
            meta[key] = content
    description = _squash(meta.get("description") or meta.get("og:description"), 1000)
    site_name = _squash(meta.get("og:site_name"), 120)

    chrome_hrefs = {a.attributes.get("href") for a in tree.css("nav a[href], header a[href], footer a[href]")}
    links: list[Link] = []
    seen: set[str] = set()
    for a in tree.css("a[href]"):
        href = (a.attributes.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            continue
        absolute = urljoin(url, href)
        p = urlparse(absolute)
        if p.scheme not in ("http", "https") or not p.netloc:
            continue
        absolute = urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", p.query, ""))
        if absolute in seen:
            continue
        seen.add(absolute)
        links.append(Link(absolute, a.text(separator="\n") or "", href in chrome_hrefs))

    tree.strip_tags(["script", "style", "noscript", "nav", "footer", "svg", "template", "iframe", "head"])
    body = tree.body
    text = _squash(body.text(separator=" ") if body else "", MAX_TEXT) or ""
    return ExtractedPage(url, title, description, site_name, text, links, html)


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _similar(candidate: str, anchors: list[str]) -> bool:
    c = _alnum(candidate)
    return any(len(a) >= 2 and len(c) >= 2 and (a in c or c in a) for a in map(_alnum, anchors) if a)


def classify_page_kind(url: str, anchor_text: str | None = None) -> str | None:
    """Page kind (careers/about/team/research/blog/news/docs/changelog) from path, subdomain or anchor text."""
    p = urlparse(url)
    segs = [s.lower() for s in p.path.split("/") if s]
    if segs and _LOCALE_SEG.match(segs[0]) and len(segs) > 1:
        segs = segs[1:]
    if segs:
        first = re.sub(r"\.html?$", "", segs[0])
        for kind, names in PAGE_KIND_SEGMENTS.items():
            if first in names:
                return kind
    else:
        label = (p.hostname or "").split(".")[0]
        for kind, names in PAGE_KIND_SEGMENTS.items():
            if label in names:
                return kind
    t = (anchor_text or "").replace("’", "'").strip().lower()
    t = re.sub(r"\s+", " ", t)
    for kind, names in PAGE_KIND_ANCHORS.items():
        if t in names:
            return kind
    return None


def discover_pages(home: ExtractedPage, domain: str) -> dict[str, str]:
    """Best same-registrable-domain URL per page kind (shallowest path wins)."""
    home_path = urlparse(home.url).path.rstrip("/")
    best: dict[str, tuple[int, int, str]] = {}
    for link in home.links:
        if registrable_domain(link.url) != domain or _SKIP_EXT.search(urlparse(link.url).path):
            continue
        p = urlparse(link.url)
        if p.path.rstrip("/") == home_path and p.netloc == urlparse(home.url).netloc:
            continue
        kind = classify_page_kind(link.url, link.text)
        if not kind:
            continue
        depth = len([s for s in p.path.split("/") if s])
        rank = (depth, len(link.url), link.url)
        if kind not in best or rank < best[kind]:
            best[kind] = rank
    return {k: v[2] for k, v in best.items()}


def github_org_from_links(links: list[Link], anchors: list[str]) -> str | None:
    """Org-level github.com/<org> link the site uses to identify itself (unique or name-matching)."""
    cands: dict[str, Link] = {}
    for link in links:
        p = urlparse(link.url)
        segs = [s for s in p.path.split("/") if s]
        if (p.hostname or "") in ("github.com", "www.github.com") and len(segs) == 1:
            login = segs[0]
            if _GH_LOGIN.match(login) and login.lower() not in GITHUB_RESERVED:
                cands.setdefault(login.lower(), link)
    return _pick_identity(cands, anchors)


def x_handle_from_links(links: list[Link], anchors: list[str]) -> str | None:
    """Profile-level x.com/<handle> or twitter.com/<handle> link (unique or name-matching)."""
    cands: dict[str, Link] = {}
    for link in links:
        p = urlparse(link.url)
        segs = [s for s in p.path.split("/") if s]
        if (p.hostname or "") in _X_HOSTS and len(segs) == 1:
            handle = segs[0].lstrip("@")
            if _X_HANDLE.match(handle) and handle.lower() not in _X_RESERVED:
                cands.setdefault(handle.lower(), link)
    return _pick_identity(cands, anchors)


def _pick_identity(cands: dict[str, Link], anchors: list[str]) -> str | None:
    """Accept a name-matching candidate, or a sole candidate placed in the site chrome."""
    matching = [k for k in cands if _similar(k, anchors)]
    if len(matching) == 1:
        return matching[0]
    if not matching and len(cands) == 1:
        only, link = next(iter(cands.items()))
        return only if link.in_chrome else None
    return None


def company_name_from(page: ExtractedPage, domain: str) -> str:
    """og:site_name, else the title segment that matches the domain, else the first non-generic segment."""
    if page.site_name and not _BLOCK_TITLES.search(page.site_name):
        return page.site_name
    label = domain.split(".")[0]
    segments = [s.strip() for s in re.split(r"\s+[|\-–—:·•]\s+", page.title or "") if s.strip()]
    for s in segments:
        if _similar(s, [label]) and len(s) <= 60:
            return s
    for s in segments:
        if s.lower() not in _GENERIC_TITLE_SEGMENTS and len(s) <= 60:
            return s
    return domain


def structured_job_links(page: ExtractedPage, domain: str) -> list[tuple[str, str]]:
    """(url, title) for anchors on a careers page that point at individual job pages on the same site."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for link in page.links:
        p = urlparse(link.url)
        if registrable_domain(link.url) != domain or not _JOB_PATH.match(p.path):
            continue
        slug = [s for s in p.path.split("/") if s][-1].lower()
        if slug in _NON_JOB_SLUGS or link.url in seen:
            continue
        first_line = next((ln.strip() for ln in link.text.splitlines() if ln.strip()), "")
        title = re.sub(r"\s+", " ", first_line)
        if not (3 <= len(title) <= 120) or not _JOB_TITLE_WORD.search(title):
            continue
        seen.add(link.url)
        out.append((link.url, title))
    return out


def select_boards(boards: list[tuple[str, str]], anchors: list[str]) -> tuple[list[tuple[str, str]], str | None]:
    """Choose job boards that belong to this company. Several unrelated boards (e.g. a portfolio page) → none."""
    if len(boards) <= 1:
        return boards, None
    matching = [b for b in boards if _similar(b[1], anchors)]
    if matching:
        return matching[:2], None
    listed = ", ".join(f"{a}:{s}" for a, s in boards[:6])
    return [], f"multiple unrelated job boards linked ({listed}); not attributing any"


class WebsiteProvider:
    name = "website"

    def __init__(self, client: HttpClient | None = None, thesis: Thesis | None = None) -> None:
        self.client = client or HttpClient(
            guard_ssrf=True, min_interval=1.0, timeout=15.0, max_retries=1, headers=IDENTITY_ENCODING
        )
        self.thesis = thesis or get_thesis()
        self.robots = RobotsCache(self.client)

    def enabled(self) -> tuple[bool, str]:
        return True, "public company websites (robots.txt respected, SSRF-guarded)"

    # ── fetching ──
    def _fetch(self, url: str, batch: ProviderBatch) -> tuple[str, str] | None:
        """(final_url, html) or None (reason appended to batch.errors)."""
        if not self.robots.allowed(url):
            batch.errors.append(f"robots.txt disallows {url}")
            return None
        try:
            resp = self.client.get(url, headers={"Accept": "text/html,application/xhtml+xml"})
        except UnsafeURLError as e:
            batch.errors.append(f"blocked unsafe URL {url}: {e}")
            return None
        except Exception as e:  # noqa: BLE001 — network failures are reported, not fatal
            batch.errors.append(f"fetch failed {url}: {type(e).__name__}")
            return None
        final = str(resp.request.url)
        if resp.status_code != 200:
            batch.errors.append(f"HTTP {resp.status_code} for {url}")
            return None
        ctype = resp.headers.get("content-type", "").lower()
        if ctype and "html" not in ctype:
            batch.errors.append(f"non-HTML content ({ctype.split(';')[0]}) at {url}")
            return None
        return final, resp.text

    def crawl(self, url: str, *, company_name: str | None = None, max_pages: int = 6) -> ProviderBatch:
        """Crawl a company site. Raises UnsafeURLError for URLs that fail the SSRF guard."""
        batch = ProviderBatch(provider=self.name)
        start = sanitize_url(url)
        assert_public_host(start)
        start_domain = company_domain(start)
        if not start_domain:
            batch.errors.append(f"{start} is not a company domain (platform or IP)")
            return batch

        got = self._fetch(start, batch)
        if not got:
            return batch
        final_url, html = got
        domain = company_domain(final_url)
        if not domain:
            batch.errors.append(f"{start} redirects to non-company host {final_url}")
            return batch
        home = extract_page(html, final_url)
        if home.title and _BLOCK_TITLES.search(home.title):
            batch.errors.append(f"bot-challenge page at {final_url} ({home.title!r}); not parsed")
            return batch

        ref = domain_ref(domain)
        name = company_name or company_name_from(home, domain)
        anchors = [domain.split(".")[0], name]
        pages: list[tuple[str, ExtractedPage]] = [("home", home)]
        targets = discover_pages(home, domain)
        for kind in FETCH_PRIORITY:
            if len(pages) >= max_pages:
                break
            if kind not in targets:
                continue
            got = self._fetch(targets[kind], batch)
            if not got:
                continue
            page_url, page_html = got
            if registrable_domain(page_url) != domain:
                # e.g. /careers → jobs.lever.co/acme: not a company page, but keep the URL for ATS detection.
                pages.append((f"{kind}_external", ExtractedPage(page_url, None, None, None, "", [], page_url)))
                continue
            pages.append((kind, extract_page(page_html, page_url)))

        for kind, page in pages:
            if kind.endswith("_external"):
                continue
            batch.sources.append({
                "url": page.url,
                "source_type": "website",
                "title": page.title,
                "snippet": page.text[:300],
                "content_hash": content_hash(page.text),
                "page_kind": kind,
                "text_excerpt": page.text[:4000],
                "company_ref": ref.key,
            })

        alt_refs: list[EntityRef] = []
        if start_domain != domain:
            alt_refs.append(domain_ref(start_domain))
        gh_org = github_org_from_links(home.links, anchors)
        x_handle = x_handle_from_links(home.links, anchors)
        if gh_org:
            alt_refs.append(gh_org_ref(gh_org))
        if x_handle:
            alt_refs.append(EntityRef(type="company", key=f"x:{x_handle}"))
        origin = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
        batch.companies.append(CompanyRecord(
            ref=ref,
            name=name,
            description=home.description,
            website=origin,
            github_org=gh_org,
            x_handle=x_handle,
            kind="company",
            alt_refs=alt_refs,
            attrs={"pages": {k: p.url for k, p in pages}},
            source_url=final_url,
        ))

        self._jobs(batch, ref, domain, anchors, pages)
        self._formation(batch, ref, pages)
        return batch

    # ── jobs / hiring ──
    def _jobs(self, batch: ProviderBatch, ref: EntityRef, domain: str, anchors: list[str],
              pages: list[tuple[str, ExtractedPage]]) -> None:
        detected = ats_mod.detect_ats([t for _, p in pages for t in (p.url, p.html)])
        boards, problem = select_boards(detected, anchors)
        if problem:
            batch.errors.append(problem)
        jobs: list[JobRecord] = []
        job_source_url: str | None = None
        sourced = False
        complete = True
        for ats_name, slug in boards:
            try:
                result = ats_mod.fetch_board(self.client, ats_name, slug, ref)
            except Exception as e:  # noqa: BLE001
                batch.errors.append(f"{ats_name} board {slug!r} fetch failed: {type(e).__name__}")
                continue
            sourced = True
            complete = complete and result.complete
            if not result.complete:
                batch.errors.append(f"{ats_name} board {slug!r} response truncated; job counts are lower bounds")
            job_source_url = job_source_url or ats_mod.board_url(ats_name, slug)
            jobs.extend(result.jobs if result.complete else [j.model_copy(update={"complete": False})
                                                            for j in result.jobs])
        if not sourced:
            careers = next((p for k, p in pages if k == "careers"), None)
            if careers is not None:
                for job_url, title in structured_job_links(careers, domain):
                    jobs.append(JobRecord(company=ref, external_id=job_url, ats="website", title=title, url=job_url))
                if jobs:
                    sourced, job_source_url = True, careers.url
        if not sourced:
            return  # no job source found: unknown, which is not the same as zero open roles

        unique = list({j.external_id: j for j in jobs}.values())
        batch.jobs.extend(unique)
        if complete:
            batch.metrics.append(MetricPoint(entity=ref, metric="open_roles", day=clock.today(), value=len(unique)))
        technical = [(j, c) for j in unique if is_technical(c := classify_role(j.title, j.department, self.thesis))]
        classes = Counter(c for _, c in technical)
        n_tech = len(technical)
        if n_tech == 0:
            return
        at_least = "" if complete else "at least "
        top = ", ".join(f"{c} ×{n}" for c, n in classes.most_common(4))
        batch.signals.append(NormalizedSignal(
            entity=ref,
            signal_type="hiring_active",
            source_type="website",
            source_url=job_source_url,
            title=f"{at_least}{n_tech} open technical role{'s' if n_tech != 1 else ''}".capitalize(),
            description=f"{at_least}{n_tech} technical of {len(unique)} open roles. Top role classes: {top}.",
            raw_value=float(n_tech),
            normalized_value=min(1.0, math.log1p(n_tech) / math.log1p(25)),
            event_at=clock.now(),
            epistemic="FACT",
            metadata={"role_classes": dict(classes), "total_open_roles": len(unique), "complete": complete,
                      "job_source": unique[0].ats, "sample_titles": [j.title for j, _ in technical[:8]]},
        ))

    # ── formation language ──
    def _formation(self, batch: ProviderBatch, ref: EntityRef, pages: list[tuple[str, ExtractedPage]]) -> None:
        """One FACT signal quoting each phrase. The quote carries the page content hash so the pipeline
        can avoid re-alerting on unchanged pages (this signal's dedup key is per day)."""
        quotes: list[dict[str, str]] = []
        for kind, page in pages:
            if kind not in FORMATION_PAGE_KINDS:
                continue
            for phrase, s, e in find_formation_matches(page.text, self.thesis):
                if phrase in WEBSITE_FORMATION_EXCLUDE or any(q["phrase"] == phrase for q in quotes):
                    continue
                quotes.append({"phrase": phrase, "quote": phrase_context(page.text, s, e), "url": page.url,
                               "page_content_hash": content_hash(page.text)})
        if not quotes:
            return
        phrases = [q["phrase"] for q in quotes]
        batch.signals.append(NormalizedSignal(
            entity=ref,
            signal_type="formation_language",
            source_type="website",
            source_url=quotes[0]["url"],
            title=f"Website uses formation language: {', '.join(repr(p) for p in phrases)}",
            description=" | ".join(f"“{q['quote']}”" for q in quotes),
            raw_value=float(len(quotes)),
            normalized_value=min(1.0, 0.5 + 0.2 * (len(quotes) - 1)),
            event_at=clock.now(),
            epistemic="FACT",
            metadata={"matched_phrases": phrases, "quotes": quotes},
        ))

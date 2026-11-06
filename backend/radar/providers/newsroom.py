"""Dated newsroom / blog / changelog items from a company's own pages (V4 Portfolio Pulse).

Companies announce launches, financings, contracts and partnerships on their own newsroom or blog. The website
crawler already fetches those index pages; this module turns them into *dated items* (title, url, date) and, when a
page declares an RSS/Atom feed on the same registrable domain, parses that feed too.

Conservative by design:

* an item needs a parseable publication date and an absolute URL on the page's own registrable domain;
* JSON-LD (Article / BlogPosting / NewsArticle / ItemList), ``<article>`` blocks with ``<time datetime>`` and list
  cards holding exactly one same-site link next to a ``<time>`` are read; nothing else is guessed;
* feeds are parsed with the standard library only after rejecting any ``<!DOCTYPE`` / ``<!ENTITY`` declaration
  (no entity expansion, no external DTDs) and capping the size; feeds on another domain are never used;
* all text is untrusted data: HTML is stripped, lengths are capped, injection patterns are recorded as flags, and
  nothing is ever executed.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

from selectolax.lexbor import LexborHTMLParser, LexborNode

from radar.llm.guard import detect_injection
from radar.providers.base import EntityRef, NormalizedSignal
from radar.providers.domains import registrable_domain
from radar.providers.http import content_hash

MAX_ITEMS = 30
MAX_TITLE = 300
MAX_SUMMARY = 500
MAX_FEED_BYTES = 2_000_000
NEWSROOM_PAGE_KINDS = ("blog", "news", "changelog")
FEED_TYPES = ("application/rss+xml", "application/atom+xml")
FEED_CONTENT_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml")
ARTICLE_TYPES = frozenset({"article", "blogposting", "newsarticle", "techarticle", "report", "pressrelease",
                           "socialmediaposting", "liveblogposting"})
SIGNAL_TYPE = "company_post"

_ATOM = "{http://www.w3.org/2005/Atom}"
_RSS1 = "{http://purl.org/rss/1.0/}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_DECL_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)
_TEXT_DATE_FORMATS = ("%B %d, %Y", "%b %d, %Y", "%b. %d, %Y", "%d %B %Y", "%d %b %Y", "%Y-%m-%d")
_MIN_YEAR = 1995


@dataclass
class NewsItem:
    title: str
    url: str
    published_at: datetime  # tz-aware UTC
    summary: str | None
    via: str  # jsonld | article | time | feed


# ── helpers ──────────────────────────────────────────────────────────────────


def _squash(s: Any, limit: int | None = None) -> str | None:
    if not isinstance(s, str) or not s:
        return None
    s = re.sub(r"\s+", " ", s).strip()
    if limit and len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or None


def _strip_html(s: str | None) -> str | None:
    if not s:
        return None
    if "<" not in s:
        return _squash(s)
    tree = LexborHTMLParser(f"<div>{s}</div>")
    tree.strip_tags(["script", "style", "noscript", "template", "iframe", "svg"])
    return _squash(tree.body.text(separator=" ") if tree.body else "")


def parse_date(value: Any, *, now: datetime | None = None) -> datetime | None:
    """ISO 8601 (date or datetime), RFC 2822 or a written date ('October 30, 2026') -> aware UTC datetime.

    Dates before 1995 or more than a day in the future (relative to ``now``, the data clock) are rejected."""
    if not isinstance(value, str):
        return None
    s = value.strip()
    if not s:
        return None
    dt: datetime | None = None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            dt = None
    if dt is None:
        cleaned = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", s)
        for fmt in _TEXT_DATE_FORMATS:
            try:
                dt = datetime.strptime(cleaned, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    dt = dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)
    if dt.year < _MIN_YEAR:
        return None
    if now is None:
        from radar import clock

        now = clock.now()
    if dt > now + timedelta(days=1):
        return None  # upcoming events / typos are not published items
    return dt


def _abs_same_site(href: Any, base_url: str, domain: str | None) -> str | None:
    if not isinstance(href, str) or not href.strip() or domain is None:
        return None
    href = href.strip()
    if href.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    p = urlparse(urljoin(base_url, href))
    if p.scheme not in ("http", "https") or not p.netloc:
        return None
    url = urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", p.query, ""))
    return url if registrable_domain(url) == domain else None


def _same_page(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return pa.netloc == pb.netloc and pa.path.rstrip("/") == pb.path.rstrip("/") and pa.query == pb.query


def _ancestors(node: LexborNode) -> Iterator[LexborNode]:
    p = node.parent
    while p is not None:
        yield p
        p = p.parent


def _types(obj: dict[str, Any]) -> set[str]:
    t = obj.get("@type")
    vals = t if isinstance(t, list) else [t]
    return {v.lower() for v in vals if isinstance(v, str)}


# ── JSON-LD ──────────────────────────────────────────────────────────────────


def _walk_jsonld(obj: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 6:
        return
    if isinstance(obj, list):
        for x in obj[:200]:
            yield from _walk_jsonld(x, depth + 1)
        return
    if not isinstance(obj, dict):
        return
    types = _types(obj)
    if types & ARTICLE_TYPES:
        yield obj
    if "itemlist" in types:
        for el in (obj.get("itemListElement") or [])[:200] if isinstance(obj.get("itemListElement"), list) else []:
            if isinstance(el, dict) and isinstance(el.get("item"), dict):
                yield from _walk_jsonld(el["item"], depth + 1)
            elif isinstance(el, dict):
                yield from _walk_jsonld(el, depth + 1)
    for key in ("@graph", "mainEntity", "hasPart", "blogPost", "blogPosts"):
        if key in obj:
            yield from _walk_jsonld(obj[key], depth + 1)


def _jsonld_items(tree: LexborHTMLParser, page_url: str, domain: str | None, now: datetime) -> list[NewsItem]:
    out: list[NewsItem] = []
    for script in tree.css('script[type="application/ld+json"]'):
        raw = script.text() or ""
        if len(raw) > 500_000:
            continue
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        for obj in _walk_jsonld(data):
            when = parse_date(obj.get("datePublished"), now=now)
            title = _squash(_strip_html(obj.get("headline") if isinstance(obj.get("headline"), str)
                                        else obj.get("name")), MAX_TITLE)
            page = obj.get("mainEntityOfPage")
            href = obj.get("url") or (page.get("@id") if isinstance(page, dict) else page)
            url = _abs_same_site(href, page_url, domain)
            if when is None or not title or url is None:
                continue
            summary = _squash(_strip_html(obj.get("description")), MAX_SUMMARY)
            out.append(NewsItem(title=title, url=url, published_at=when, summary=summary, via="jsonld"))
    return out


# ── HTML markup ──────────────────────────────────────────────────────────────


def _time_value(node: LexborNode, now: datetime) -> datetime | None:
    return parse_date(node.attributes.get("datetime"), now=now) or parse_date(_squash(node.text()), now=now)


def _links(node: LexborNode, page_url: str, domain: str | None) -> list[tuple[str, LexborNode]]:
    nodes = [node] if node.tag == "a" else []
    nodes += list(node.css("a[href]"))
    out: list[tuple[str, LexborNode]] = []
    seen: set[str] = set()
    for a in nodes:
        url = _abs_same_site(a.attributes.get("href"), page_url, domain)
        if url is None or _same_page(url, page_url) or url in seen:
            continue
        seen.add(url)
        out.append((url, a))
    return out


def _heading_text(node: LexborNode) -> str | None:
    for h in node.css("h1, h2, h3, h4, h5"):
        t = _squash(h.text(separator=" "), MAX_TITLE)
        if t:
            return t
    return None


def _link_text(a: LexborNode) -> str | None:
    lines = [ln.strip() for ln in (a.text(separator="\n") or "").splitlines() if ln.strip()]
    return _squash(max(lines, key=len), MAX_TITLE) if lines else None


def _summary(node: LexborNode) -> str | None:
    p = node.css_first("p")
    return _squash(p.text(separator=" "), MAX_SUMMARY) if p is not None else None


def _article_items(tree: LexborHTMLParser, page_url: str, domain: str | None, now: datetime) -> list[NewsItem]:
    out: list[NewsItem] = []
    for art in tree.css("article"):
        t = art.css_first("time")
        when = _time_value(t, now) if t is not None else None
        if when is None:
            continue
        links = _links(art, page_url, domain)
        heading_link = None
        for h in art.css("h1, h2, h3, h4, h5"):
            for a in h.css("a[href]"):
                u = _abs_same_site(a.attributes.get("href"), page_url, domain)
                if u and not _same_page(u, page_url):
                    heading_link = (u, a)
                    break
            if heading_link:
                break
        pick = heading_link or (links[0] if len(links) == 1 else None)
        if pick is None:
            continue
        url, a = pick
        title = _heading_text(art) or _link_text(a)
        if not title or len(title) < 3:
            continue
        out.append(NewsItem(title=title, url=url, published_at=when, summary=_summary(art), via="article"))
    return out


_CARD_TAGS = frozenset({"li", "div", "section", "a", "tr"})


def _card_items(tree: LexborHTMLParser, page_url: str, domain: str | None, now: datetime) -> list[NewsItem]:
    """A <time> inside a small card (li/div/a) that holds exactly one same-site link."""
    out: list[NewsItem] = []
    for t in tree.css("time"):
        anc = list(_ancestors(t))
        tags = {a.tag for a in anc}
        if tags & {"article", "nav", "footer"}:
            continue  # articles are handled above; navigation and footers are not posts
        when = _time_value(t, now)
        if when is None:
            continue
        for node in anc[:4]:
            if node.tag in ("body", "html", "main"):
                break
            links = _links(node, page_url, domain)
            if len(links) > 1:
                break  # the container groups several posts: ambiguous, never guess
            if len(links) == 1 and node.tag in _CARD_TAGS:
                url, a = links[0]
                title = _heading_text(node) or _link_text(a)
                date_text = _squash(t.text())
                if title and date_text and title == date_text:
                    title = None
                if title and len(title) >= 3:
                    out.append(NewsItem(title=title, url=url, published_at=when, summary=_summary(node),
                                        via="time"))
                break
    return out


def _merge(groups: Iterable[list[NewsItem]], limit: int) -> list[NewsItem]:
    seen: dict[str, NewsItem] = {}
    for group in groups:
        for it in group:
            key = it.url.rstrip("/")
            if key not in seen:
                seen[key] = it
            elif not seen[key].summary and it.summary:
                seen[key].summary = it.summary
    items = sorted(seen.values(), key=lambda i: (-i.published_at.timestamp(), i.url))
    return items[:limit]


def extract_items(html: str, page_url: str, *, limit: int = MAX_ITEMS, now: datetime | None = None
                  ) -> list[NewsItem]:
    """Dated items listed on a blog / news / changelog page (newest first, at most ``limit``)."""
    if not html:
        return []
    if now is None:
        from radar import clock

        now = clock.now()
    domain = registrable_domain(page_url)
    tree = LexborHTMLParser(html[:3_000_000])
    jsonld = _jsonld_items(tree, page_url, domain, now)
    articles = _article_items(tree, page_url, domain, now)
    cards = _card_items(tree, page_url, domain, now)
    return _merge([jsonld, articles, cards], limit)


def merge_items(*groups: list[NewsItem], limit: int = MAX_ITEMS) -> list[NewsItem]:
    """Union of item lists (first occurrence of a url wins), newest first, capped."""
    return _merge(groups, limit)


# ── feeds ────────────────────────────────────────────────────────────────────


def feed_links(html: str, page_url: str) -> list[str]:
    """RSS/Atom feeds the page declares (``<link rel=alternate type=...>``), same registrable domain only."""
    if not html:
        return []
    domain = registrable_domain(page_url)
    out: list[str] = []
    for link in LexborHTMLParser(html[:3_000_000]).css("link[href]"):
        rel = (link.attributes.get("rel") or "").lower().split()
        typ = (link.attributes.get("type") or "").lower().split(";")[0].strip()
        if "alternate" not in rel or typ not in FEED_TYPES:
            continue
        url = _abs_same_site(link.attributes.get("href"), page_url, domain)
        if url and url not in out:
            out.append(url)
    return out


def _text(el: ET.Element | None) -> str | None:
    if el is None:
        return None
    return "".join(el.itertext())


def parse_feed(xml_text: str, feed_url: str, *, limit: int = MAX_ITEMS, max_bytes: int = MAX_FEED_BYTES,
               now: datetime | None = None) -> list[NewsItem]:
    """Items of an RSS 2.0 / RSS 1.0 / Atom feed. Returns [] for documents with a DOCTYPE or ENTITY declaration
    (no entity expansion, ever), oversized documents and unparseable XML. Item links must be on the feed's own
    registrable domain."""
    if not xml_text or len(xml_text.encode("utf-8", "ignore")) > max_bytes or _DECL_RE.search(xml_text):
        return []
    if now is None:
        from radar import clock

        now = clock.now()
    domain = registrable_domain(feed_url)
    try:
        root = ET.fromstring(xml_text.lstrip("\ufeff"))
    except ET.ParseError:
        return []
    items: list[NewsItem] = []
    if root.tag == f"{_ATOM}feed":
        for entry in root.findall(f"{_ATOM}entry"):
            href = None
            for link in entry.findall(f"{_ATOM}link"):
                if link.get("rel") in (None, "alternate") and link.get("href"):
                    href = link.get("href")
                    break
            when = (parse_date(_text(entry.find(f"{_ATOM}published")), now=now)
                    or parse_date(_text(entry.find(f"{_ATOM}updated")), now=now))
            title = _squash(_strip_html(_text(entry.find(f"{_ATOM}title"))), MAX_TITLE)
            summary = _squash(_strip_html(_text(entry.find(f"{_ATOM}summary"))
                                          or _text(entry.find(f"{_ATOM}content"))), MAX_SUMMARY)
            url = _abs_same_site(href, feed_url, domain)
            if when and title and url:
                items.append(NewsItem(title=title, url=url, published_at=when, summary=summary, via="feed"))
    else:
        entries = root.findall("./channel/item") or root.findall(f"{_RSS1}item") or root.findall(".//item")
        for it in entries:
            title = _squash(_strip_html(_text(it.find("title")) or _text(it.find(f"{_RSS1}title"))), MAX_TITLE)
            href = _text(it.find("link")) or _text(it.find(f"{_RSS1}link"))
            when = (parse_date(_text(it.find("pubDate")), now=now)
                    or parse_date(_text(it.find(f"{_DC}date")), now=now))
            summary = _squash(_strip_html(_text(it.find("description")) or _text(it.find(f"{_RSS1}description"))),
                              MAX_SUMMARY)
            url = _abs_same_site((href or "").strip(), feed_url, domain)
            if when and title and url:
                items.append(NewsItem(title=title, url=url, published_at=when, summary=summary, via="feed"))
    return _merge([items], limit)


# ── signals ──────────────────────────────────────────────────────────────────


def item_signals(items: Iterable[NewsItem], ref: EntityRef, *, page_kind: str, page_url: str
                 ) -> tuple[list[NormalizedSignal], list[dict[str, Any]]]:
    """One FACT ``company_post`` signal and one source per item. Sources carry no ``page_kind`` (that key would
    make the ingest pipeline treat the post as a crawled site page)."""
    signals: list[NormalizedSignal] = []
    sources: list[dict[str, Any]] = []
    for it in items:
        flags = detect_injection(f"{it.title}\n{it.summary or ''}")
        meta: dict[str, Any] = {"page_kind": page_kind, "page_url": page_url, "via": it.via,
                                "published_at": it.published_at.isoformat()}
        if flags:
            meta["injection_flags"] = flags[:5]
        signals.append(NormalizedSignal(
            entity=ref,
            signal_type=SIGNAL_TYPE,
            source_type="website",
            source_url=it.url,
            title=it.title,
            description=(it.summary or None) and it.summary[:MAX_SUMMARY],
            event_at=it.published_at,
            confidence=1.0,
            epistemic="FACT",
            metadata=meta,
        ))
        sources.append({
            "url": it.url,
            "source_type": "website",
            "title": it.title,
            "snippet": (it.summary or it.title)[:300],
            "content_hash": content_hash(f"{it.title}\n{it.url}\n{it.published_at.isoformat()}"),
            "company_ref": ref.key,
        })
    return signals, sources

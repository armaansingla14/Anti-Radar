"""Public applicant-tracking-system job boards: Greenhouse, Lever, Ashby.

Only the documented, unauthenticated job-board JSON APIs are used. Every JobRecord
comes verbatim from the board; nothing is inferred or synthesized.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import quote, unquote

from dateutil import parser as dateparser

from radar.providers.base import EntityRef, JobRecord
from radar.providers.http import MAX_BODY_BYTES, HttpClient

ATS = Literal["greenhouse", "lever", "ashby"]

_SLUG = r"([A-Za-z0-9][A-Za-z0-9_.%\-]{0,99})"
_PATTERNS: list[tuple[ATS, re.Pattern[str]]] = [
    (
        "greenhouse",
        re.compile(
            r"(?:job-boards|boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?\?(?:[^\"'\s<>]*?&(?:amp;)?)?for=)?"
            + _SLUG,
            re.IGNORECASE,
        ),
    ),
    ("lever", re.compile(r"jobs\.lever\.co/" + _SLUG, re.IGNORECASE)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/" + _SLUG, re.IGNORECASE)),
]
_RESERVED_SLUGS = {"embed", "v1", "api", "static", "assets", "favicon.ico", "robots.txt"}
_SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. \-]{0,99}$")

# HttpClient re-wraps already-decoded stream bytes with the original Content-Encoding header,
# which breaks gzip responses; asking for identity encoding sidesteps that.
IDENTITY_ENCODING = {"Accept-Encoding": "identity"}

API_URLS: dict[str, str] = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=false",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}
BOARD_URLS: dict[str, str] = {
    "greenhouse": "https://job-boards.greenhouse.io/{slug}",
    "lever": "https://jobs.lever.co/{slug}",
    "ashby": "https://jobs.ashbyhq.com/{slug}",
}


def detect_ats(sources: str | Iterable[str]) -> list[tuple[str, str]]:
    """Find (ats, slug) job boards referenced in a URL, HTML document, or list of either.

    Deduplicated case-insensitively, in order of first appearance.
    """
    texts = [sources] if isinstance(sources, str) else list(sources)
    found: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for text in texts:
        hits = sorted(
            (m.start(), ats, unquote(m.group(1)).rstrip("."))
            for ats, pattern in _PATTERNS
            for m in pattern.finditer(text or "")
        )
        for _pos, ats, slug in hits:
            key = (ats, slug.lower())
            if slug.lower() in _RESERVED_SLUGS or not _SLUG_OK.match(slug) or key in seen:
                continue
            seen.add(key)
            found.append((ats, slug))
    return found


def board_url(ats: str, slug: str) -> str:
    return BOARD_URLS[ats].format(slug=quote(slug, safe=""))


def _dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):  # Lever: epoch milliseconds
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        d = dateparser.isoparse(str(value))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except (ValueError, OverflowError, OSError):
        return None


def _clean(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def parse_greenhouse(data: dict[str, Any], company: EntityRef) -> list[JobRecord]:
    out: list[JobRecord] = []
    for j in data.get("jobs") or []:
        title = _clean(j.get("title"))
        if not title or j.get("id") is None:
            continue
        department = None
        if j.get("departments"):
            department = _clean(j["departments"][0].get("name"))
        if not department:
            for md in j.get("metadata") or []:
                if "department" in str(md.get("name", "")).lower() and isinstance(md.get("value"), str):
                    department = _clean(md["value"])
                    break
        out.append(
            JobRecord(
                company=company,
                external_id=f"greenhouse:{j['id']}",
                ats="greenhouse",
                title=title,
                department=department,
                location=_clean((j.get("location") or {}).get("name")),
                url=j.get("absolute_url"),
                posted_at=_dt(j.get("first_published")),
            )
        )
    return out


def parse_lever(data: list[dict[str, Any]], company: EntityRef) -> list[JobRecord]:
    out: list[JobRecord] = []
    for j in data or []:
        title = _clean(j.get("text"))
        if not title or not j.get("id"):
            continue
        cats = j.get("categories") or {}
        out.append(
            JobRecord(
                company=company,
                external_id=f"lever:{j['id']}",
                ats="lever",
                title=title,
                department=_clean(cats.get("department")) or _clean(cats.get("team")),
                location=_clean(cats.get("location")),
                url=j.get("hostedUrl"),
                posted_at=_dt(j.get("createdAt")),
            )
        )
    return out


def parse_ashby(data: dict[str, Any], company: EntityRef) -> list[JobRecord]:
    out: list[JobRecord] = []
    for j in data.get("jobs") or []:
        title = _clean(j.get("title"))
        if not title or not j.get("id") or j.get("isListed") is False:
            continue
        out.append(
            JobRecord(
                company=company,
                external_id=f"ashby:{j['id']}",
                ats="ashby",
                title=title,
                department=_clean(j.get("department")) or _clean(j.get("team")),
                location=_clean(j.get("location")),
                url=j.get("jobUrl"),
                posted_at=_dt(j.get("publishedAt")),
            )
        )
    return out


_PARSERS = {"greenhouse": parse_greenhouse, "lever": parse_lever, "ashby": parse_ashby}
LEVER_PAGE = 100
LEVER_MAX_PAGES = 20


@dataclass
class BoardResult:
    jobs: list[JobRecord]
    complete: bool  # False when the response hit the HTTP body cap and only whole postings were kept


def salvage_array(text: str, key: str | None) -> list[Any]:
    """Recover the complete leading elements of a JSON array from a truncated document.

    `key` names the array inside a top-level object (e.g. "jobs"); None means a top-level array.
    """
    if key is None:
        start = text.find("[")
    else:
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*\[', text)
        start = m.end() - 1 if m else -1
    if start < 0:
        return []
    decoder = json.JSONDecoder()
    items: list[Any] = []
    pos = start + 1
    while True:
        while pos < len(text) and text[pos] in " \t\r\n,":
            pos += 1
        if pos >= len(text) or text[pos] == "]":
            break
        try:
            item, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            break
        items.append(item)
    return items


def _get_json(client: HttpClient, url: str, key: str | None) -> tuple[Any, bool]:
    """(data, complete). Falls back to salvaging whole elements when the body was truncated."""
    resp = client.get(url, headers={"Accept": "application/json", **IDENTITY_ENCODING})
    resp.raise_for_status()
    try:
        return resp.json(), True
    except json.JSONDecodeError:
        if len(resp.content) < MAX_BODY_BYTES:
            raise
    items = salvage_array(resp.text, key)
    if not items:
        raise ValueError(f"truncated, unparseable job-board response from {url}")
    return ({key: items} if key else items), False


def fetch_board(client: HttpClient, ats: str, slug: str, company: EntityRef) -> BoardResult:
    """Fetch and normalize all open postings on a public board.

    Raises ValueError for an unknown ATS / malformed slug and httpx errors for HTTP failures
    (a 404 means the board does not exist — callers should record it, not treat it as zero jobs).
    """
    if ats not in API_URLS:
        raise ValueError(f"unsupported ATS: {ats}")
    if not _SLUG_OK.match(slug):
        raise ValueError(f"malformed job-board slug: {slug!r}")
    url = API_URLS[ats].format(slug=quote(slug, safe=""))
    if ats != "lever":
        data, complete = _get_json(client, url, "jobs")
        return BoardResult(_PARSERS[ats](data, company), complete)
    jobs: list[JobRecord] = []
    complete = True
    for page in range(LEVER_MAX_PAGES):
        data, page_complete = _get_json(client, f"{url}&limit={LEVER_PAGE}&skip={page * LEVER_PAGE}", None)
        jobs.extend(parse_lever(data, company))
        complete = complete and page_complete
        if len(data) < LEVER_PAGE or not page_complete:
            break
    else:
        complete = False
    return BoardResult(jobs, complete)


def fetch_jobs(client: HttpClient, ats: str, slug: str, company: EntityRef) -> list[JobRecord]:
    """All open postings on a board (see fetch_board for completeness information)."""
    return fetch_board(client, ats, slug, company).jobs

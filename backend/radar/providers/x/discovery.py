"""X discovery via the official API v2: phrase × thesis-term recent search plus optional seed accounts.

Configuration lives in ``config/x_seeds.yaml`` (next to the thesis file). Only runs with an
``OfficialXProvider`` (i.e. when ``X_BEARER_TOKEN`` is set).

Storage is minimal by design: a post is kept only when it classifies as a formation / launch /
hiring post or literally contains one of the configured discovery phrases; everything else the
search returned is counted and dropped. Each kept post becomes a FACT signal on ``x:<handle>``
with its real ``https://x.com/<handle>/status/<id>`` URL. The run stops cleanly (no error) when
the API reports the rate-limit window exhausted.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from radar import clock
from radar.providers.base import ProviderBatch
from radar.providers.x.api import OfficialXProvider
from radar.providers.x.base import XPost, XUser, classify_post, posts_to_batch, valid_handle, x_person_ref
from radar.settings import get_settings
from radar.thesis.loader import Thesis, get_thesis

log = logging.getLogger("radar.providers.x.discovery")

SOURCE_LABEL = "x_api"
COMPANY_SLOT = "{company}"
RATE_LIMIT_ERROR = "rate limited (429)"


class XLimits(BaseModel):
    request_budget: int = Field(8, ge=0, le=500)
    max_results: int = Field(25, ge=10, le=100)
    lookback_days: int = Field(7, ge=1, le=7)
    max_query_chars: int = Field(512, ge=64, le=4096)
    seed_posts_per_account: int = Field(10, ge=5, le=100)
    max_posts_per_run: int = Field(150, ge=1, le=2000)


class XSeeds(BaseModel):
    limits: XLimits = Field(default_factory=XLimits)
    query_suffix: str = "-is:retweet -is:reply lang:en"
    phrases: list[str] = Field(default_factory=list)
    departure_companies: list[str] = Field(default_factory=list)
    thesis_terms: list[str] = Field(default_factory=list)
    seed_accounts: list[str] = Field(default_factory=list)

    @field_validator("seed_accounts")
    @classmethod
    def _handles(cls, v: list[str]) -> list[str]:
        out: list[str] = []
        for h in v or []:
            h = str(h).strip().lstrip("@")
            if not valid_handle(h):
                raise ValueError(f"invalid X handle in seed_accounts: {h!r}")
            if h.lower() not in {o.lower() for o in out}:
                out.append(h)
        return out


class XQuery(BaseModel):
    phrase: str
    text: str


def x_seeds_path() -> Path:
    return Path(get_settings().thesis_path).parent / "x_seeds.yaml"


def load_x_seeds(path: Path | None = None) -> XSeeds:
    """Parse ``config/x_seeds.yaml`` (missing file -> defaults with no phrases, i.e. no queries)."""
    import yaml

    p = Path(path or x_seeds_path())
    if not p.exists():
        return XSeeds()
    with p.open(encoding="utf-8") as f:
        return XSeeds.model_validate(yaml.safe_load(f) or {})


# ── query construction ──────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("’", "'").replace("‘", "'")).strip().lower()


def phrase_alternatives(phrase: str, seeds: XSeeds) -> list[str]:
    """Literal alternatives of one configured phrase (``{company}`` expands to departure_companies)."""
    if COMPANY_SLOT in phrase:
        return [phrase.replace(COMPANY_SLOT, c).strip() for c in seeds.departure_companies if c.strip()]
    return [phrase.strip()] if phrase.strip() else []


def _term(t: str) -> str:
    t = t.strip().replace('"', "")
    return t if re.fullmatch(r"[A-Za-z0-9_]+", t) else f'"{t}"'


def _or_group(items: list[str]) -> str:
    return items[0] if len(items) == 1 else "(" + " OR ".join(items) + ")"


def build_queries(seeds: XSeeds) -> list[XQuery]:
    """phrase × thesis-term chunk queries, each within ``max_query_chars``. Phrases whose own group
    does not fit are skipped (logged)."""
    max_chars = seeds.limits.max_query_chars
    suffix = seeds.query_suffix.strip()
    terms = [_term(t) for t in seeds.thesis_terms if t and t.strip()]
    out: list[XQuery] = []
    for phrase in seeds.phrases:
        alts = phrase_alternatives(phrase, seeds)
        if not alts:
            continue
        group = _or_group([f'"{a.replace(chr(34), "")}"' for a in alts])
        fixed = len(group) + (len(suffix) + 1 if suffix else 0)
        if not terms:
            text = f"{group} {suffix}".strip()
            if len(text) <= max_chars:
                out.append(XQuery(phrase=phrase, text=text))
            continue
        room = max_chars - fixed - 3  # space + parentheses
        chunk: list[str] = []
        chunks: list[list[str]] = []
        for t in terms:
            if len(" OR ".join([*chunk, t])) <= room:
                chunk.append(t)
            elif chunk:
                chunks.append(chunk)
                chunk = [t] if len(t) <= room else []
        if chunk:
            chunks.append(chunk)
        if not chunks:
            log.warning("x discovery: phrase %r does not fit max_query_chars=%d; skipped", phrase, max_chars)
            continue
        for c in chunks:
            out.append(XQuery(phrase=phrase, text=" ".join(p for p in (group, _or_group(c), suffix) if p)))
    return out


def rotate(items: list[Any], n: int, day: date) -> list[Any]:
    """Deterministic window of ``n`` items that advances by ``n`` each day (full coverage over days)."""
    if n <= 0 or not items:
        return []
    if n >= len(items):
        return list(items)
    start = (day.toordinal() * n) % len(items)
    return [items[(start + i) % len(items)] for i in range(n)]


def matched_discovery_phrases(text: str, seeds: XSeeds) -> list[str]:
    """Configured phrase alternatives literally present (word-bounded, case-insensitive) in ``text``."""
    norm = _norm(text)
    hits: list[str] = []
    for phrase in seeds.phrases:
        for alt in phrase_alternatives(phrase, seeds):
            if re.search(r"(?<!\w)" + re.escape(_norm(alt)) + r"(?!\w)", norm):
                hits.append(alt)
    return hits


def keep_post(post: XPost, thesis: Thesis, seeds: XSeeds) -> tuple[bool, list[str]]:
    """(keep?, matched discovery phrases). Generic posts with no discovery phrase are dropped."""
    signal_type, _ = classify_post(post.text, thesis)
    discovery = matched_discovery_phrases(post.text, seeds)
    return (signal_type != "x_post" or bool(discovery)), discovery


# ── discovery run ───────────────────────────────────────────────────────────


def discover(
    provider: OfficialXProvider,
    seeds: XSeeds,
    *,
    thesis: Thesis | None = None,
    day: date | None = None,
    now: datetime | None = None,
) -> tuple[ProviderBatch, dict[str, Any]]:
    """One budgeted discovery pass. Returns (batch, info) — info is JSON-safe run metadata."""
    thesis = thesis or get_thesis()
    day = day or clock.today()
    now = now or clock.wall_now()
    lim = seeds.limits
    budget = lim.request_budget
    batch = ProviderBatch(provider="x")
    info: dict[str, Any] = {"request_budget": budget, "queries": [], "seed_accounts": [], "posts_seen": 0,
                            "posts_kept": 0, "posts_dropped": 0, "stopped": None, "rate_limited_until": None}
    seen: set[str] = set()
    kept: dict[str, list[tuple[XPost, dict[str, Any]]]] = {}
    users: dict[str, XUser] = {}

    def used() -> int:
        return provider.requests

    def note_error(label: str) -> None:
        err = provider.last_error
        provider.last_error = None
        if err and err != RATE_LIMIT_ERROR:
            batch.errors.append(f"{label}: {err}")

    def take(posts: list[XPost], meta: dict[str, Any]) -> int:
        n = 0
        for p in posts:
            if p.id in seen:
                continue
            seen.add(p.id)
            info["posts_seen"] += 1
            ok, discovery = keep_post(p, thesis, seeds)
            if not ok or not p.url or info["posts_kept"] >= lim.max_posts_per_run:
                info["posts_dropped"] += 1
                continue
            kept.setdefault(p.author_username.lower(), []).append((p, {**meta, "discovery_phrases": discovery}))
            info["posts_kept"] += 1
            n += 1
        return n

    queries = build_queries(seeds)
    # Seeds cost 2 requests each; when search queries exist they get at most half the budget.
    seed_cap = (budget // 2 if queries else budget) // 2
    for handle in rotate(seeds.seed_accounts, min(len(seeds.seed_accounts), seed_cap), day):
        if provider.rate_limited or used() + 2 > budget:
            break
        provider.last_error = None
        user = provider.get_user(handle)
        posts = provider.recent_posts(handle, limit=lim.seed_posts_per_account) if user else []
        note_error(f"seed @{handle}")
        if user:
            users[user.username.lower()] = user
        info["seed_accounts"].append({"handle": handle, "posts": len(posts),
                                      "kept": take(posts, {"ingest": "x_seed", "seed_account": True})})

    start_time = now - timedelta(days=lim.lookback_days) + timedelta(minutes=1)  # API rejects > 7 days back
    for q in rotate(queries, max(0, budget - used()), day):
        if provider.rate_limited or used() >= budget:
            break
        provider.last_error = None
        posts, authors = provider.search_recent_with_authors(q.text, lim.max_results, start_time=start_time)
        note_error(f"search {q.phrase!r}")
        users.update({k: v for k, v in authors.items() if k not in users})
        info["queries"].append({"phrase": q.phrase, "query": q.text, "posts": len(posts),
                                "kept": take(posts, {"ingest": "x_search", "query_phrase": q.phrase})})

    for handle, items in kept.items():
        user = users.get(handle)
        ref = x_person_ref(items[0][0].author_username)
        if user is not None:  # profile record + follower metric once per author
            batch.extend(posts_to_batch([], user, ref, SOURCE_LABEL, thesis=thesis))
        for post, meta in items:
            batch.extend(posts_to_batch([post], None, ref, SOURCE_LABEL, thesis=thesis, extra_metadata=meta))

    info["requests"] = used()
    info["rate_limit_remaining"] = provider.rate_limit_remaining
    if provider.rate_limited:
        info["stopped"] = "rate_limited"
        info["rate_limited_until"] = provider.rate_limited_until.isoformat() if provider.rate_limited_until else None
    elif used() >= budget and budget > 0:
        info["stopped"] = "request_budget"
    batch.provider = "x"
    return batch, info

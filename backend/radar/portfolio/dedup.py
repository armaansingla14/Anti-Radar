"""Stage 3: one event per real-world happening.

Two candidates about the same company merge when they are within ``MERGE_DAYS`` of each other, belong to the same
event type (or one is a Hacker News story about the other), and either share a URL (an HN story linking the
company's own post, a release URL quoted in a post) or have titles with token Jaccard >= ``JACCARD``. Merging is
transitive (union-find). The merged event keeps every source and the sum of raw changes; its title, type and date
come from the most authoritative member (company post > registry > code > third party), its date is the earliest.

NOISE candidates merge only with each other, so a noisy duplicate can never upgrade or hide a real event. Hiring
clusters are already one candidate per cluster and are never merged with posts.

``event_key`` is the stable dedup key of the stored MaterialEvent: subject + subtype + primary URL (or normalised
title + ISO week when the event has no URL, e.g. a hiring cluster).
"""

from __future__ import annotations

import hashlib
import re
from datetime import timedelta

from radar.portfolio.types import NOISE_TYPE, Candidate

MERGE_DAYS = 7
JACCARD = 0.5
PUBLISHER_RANK = {"company": 0, "registry": 1, "third_party": 2}
TYPE_RANK = {"RISK": 0, "FINANCING": 1, "COMMERCIAL": 2, "REGULATORY": 3, "TALENT": 4, "PRODUCT": 5, "TECHNICAL": 6,
             "STRATEGIC": 7, "COMPETITIVE": 8, NOISE_TYPE: 9}
_STOP = frozenset("a an and the of for to in on with at by from our we is are new our your its this that via".split())
_URL_NORM = re.compile(r"^https?://(www\.)?")


def tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", (text or "").lower()) if t not in _STOP and len(t) > 1}


def jaccard(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _norm_url(u: str) -> str:
    return _URL_NORM.sub("", u.strip().lower()).rstrip("/").split("#")[0]


def _urls(c: Candidate) -> set[str]:
    out = {_norm_url(u) for u in c.urls}
    link = c.meta.get("link")
    if isinstance(link, str) and link:
        out.add(_norm_url(link))
    return out


def _is_hn(c: Candidate) -> bool:
    return any(s.source_type == "hackernews" for s in c.sources)


def _compatible(a: Candidate, b: Candidate) -> bool:
    if a.company_id != b.company_id:
        return False
    if (a.event_type == NOISE_TYPE) != (b.event_type == NOISE_TYPE):
        return False
    if a.subtype in _CLUSTERS or b.subtype in _CLUSTERS:
        return False
    if abs(a.event_at - b.event_at) > timedelta(days=MERGE_DAYS):
        return False
    same_type = a.event_type == b.event_type
    if not same_type and not (_is_hn(a) or _is_hn(b)):
        return False
    if _urls(a) & _urls(b):
        return True
    return jaccard(a.title, b.title) >= JACCARD


_CLUSTERS = frozenset({"specialized_hiring_cluster", "new_role_class", "hiring_acceleration", "role_closures",
                       "single_postings"})


def _primary_key(c: Candidate) -> tuple:
    pub = min((PUBLISHER_RANK.get(s.publisher, 3) for s in c.sources), default=3)
    return (TYPE_RANK.get(c.event_type, 9) if c.event_type != NOISE_TYPE else 99, pub, c.event_at, c.title)


def merge(cands: list[Candidate]) -> list[Candidate]:
    """Merged candidates (deterministic order: event_at, company, type, title)."""
    items = sorted(cands, key=lambda c: (c.event_at, str(c.company_id), c.event_type, c.subtype, c.title))
    parent = list(range(len(items)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_company: dict = {}
    for i, c in enumerate(items):
        by_company.setdefault(c.company_id, []).append(i)
    for idx in by_company.values():
        for x in range(len(idx)):
            for y in range(x + 1, len(idx)):
                a, b = items[idx[x]], items[idx[y]]
                if b.event_at - a.event_at > timedelta(days=MERGE_DAYS):
                    break
                if _compatible(a, b):
                    ra, rb = find(idx[x]), find(idx[y])
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
    groups: dict[int, list[Candidate]] = {}
    for i, c in enumerate(items):
        groups.setdefault(find(i), []).append(c)
    out = [combine(g) for g in groups.values()]
    out.sort(key=lambda c: (c.event_at, str(c.company_id), c.event_type, c.subtype, c.title))
    return out


def combine(group: list[Candidate]) -> Candidate:
    if len(group) == 1:
        return group[0]
    primary = min(group, key=_primary_key)
    sources, seen = [], set()
    for c in sorted(group, key=_primary_key):
        for s in c.sources:
            k = s.url or s.ref  # the same page captured twice (e.g. two captures) is one source
            if k not in seen:
                seen.add(k)
                sources.append(s)
    sources.sort(key=lambda s: PUBLISHER_RANK.get(s.publisher, 3))  # the company's own source first, then registry, then third party
    meta = {}
    for c in sorted(group, key=_primary_key, reverse=True):
        meta.update(c.meta)
    meta["merged_titles"] = sorted({c.title for c in group})[:10]
    return Candidate(
        company_id=primary.company_id, event_type=primary.event_type, subtype=primary.subtype, title=primary.title,
        event_at=min(c.event_at for c in group), sources=sources,
        epistemic="FACT" if any(c.epistemic == "FACT" for c in group) else primary.epistemic,
        confidence=max(c.confidence for c in group), description=primary.description,
        raw_count=sum(c.raw_count for c in group), meta=meta, rule=primary.rule,
        in_window=any(c.in_window for c in group),
    )


def event_key(c: Candidate) -> str:
    primary = next((s.url for s in sorted(c.sources, key=lambda s: PUBLISHER_RANK.get(s.publisher, 3)) if s.url),
                   None)
    if c.subtype in _CLUSTERS or not primary:
        year, week, _ = c.event_at.isocalendar()
        basis = f"{c.company_id}|{c.subtype}|{c.meta.get('role_class') or ''}|{' '.join(sorted(tokens(c.title)))}" \
                f"|{year}-W{week:02d}"
        if c.subtype in _CLUSTERS:
            basis = f"{c.company_id}|{c.subtype}|{c.meta.get('role_class') or ''}|{year}-W{week:02d}"
    else:
        basis = f"{c.company_id}|{c.subtype}|{_norm_url(primary)}"
    return hashlib.sha256(basis.encode()).hexdigest()

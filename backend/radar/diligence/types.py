"""Plain data shapes shared by collection and analysis, plus small pure helpers.

``ArtifactDraft`` is what collection produces and what a capture file stores; analysis only ever sees these
(and a ``CompanyFacts`` snapshot), never the database or the network.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from radar.diligence.taxonomy import Publisher, Relation, Strength


@dataclass
class ArtifactDraft:
    """One observed public artifact. ``text`` is untrusted and only ever quoted, never executed."""

    key: str
    kind: str
    publisher: str
    url: str
    title: str | None = None
    fetched_at: datetime | None = None
    http_status: int | None = None
    content_hash: str | None = None
    text: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fetched_at"] = self.fetched_at.isoformat() if self.fetched_at else None
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ArtifactDraft:
        return cls(
            key=str(d["key"]), kind=str(d["kind"]), publisher=str(d["publisher"]), url=str(d.get("url") or ""),
            title=d.get("title"), fetched_at=parse_dt(d.get("fetched_at")), http_status=d.get("http_status"),
            content_hash=d.get("content_hash"), text=d.get("text") or "", data=dict(d.get("data") or {}),
            flags=list(d.get("flags") or []),
        )


@dataclass
class CompanyFacts:
    """What we knew about the company when collecting (identity + metric history), stored with a capture so a
    replay does not depend on the database's current state."""

    name: str
    slug: str
    website: str | None = None
    domain: str | None = None
    github_org: str | None = None
    thesis_tags: list[str] = field(default_factory=list)
    # Lower-cased GitHub / X handles of people linked to the company: a launch post by one of them is a
    # company statement, not independent evidence.
    team_handles: list[str] = field(default_factory=list)
    # "<metric>:<owner/repo>" -> [[iso day, value], ...] (oldest first), from MetricSnapshot
    metrics: dict[str, list[list[Any]]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CompanyFacts:
        return cls(
            name=str(d.get("name") or ""), slug=str(d.get("slug") or ""), website=d.get("website"),
            domain=d.get("domain"), github_org=d.get("github_org"), thesis_tags=list(d.get("thesis_tags") or []),
            team_handles=list(d.get("team_handles") or []),
            metrics={k: [list(p) for p in v] for k, v in (d.get("metrics") or {}).items()},
        )


@dataclass
class ClaimDraft:
    """A material claim plus everything analysis attaches to it."""

    text: str
    claim_type: str
    source_key: str
    source_url: str
    source_quote: str
    observed_at: datetime | None = None
    materiality: float = 0.0
    position: int = 0  # offset of the quote in its artifact (stable ordering)
    source_rank: int = 9
    extracted_by: str = "rules"
    company_evidence: list[dict[str, Any]] = field(default_factory=list)
    # filled by analysis
    ordinal: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    testability: str = ""
    status: str = ""
    status_reason: str = ""
    rule: str = ""
    confidence: str = ""
    test_plan: dict[str, Any] | None = None


# ── evidence items ───────────────────────────────────────────────────────────


def evidence_item(
    art: ArtifactDraft,
    *,
    relation: str,
    strength: str,
    matcher: str,
    note: str,
    quote: str = "",
    verifies: bool = False,
    observed_at: datetime | None = None,
    gap: bool = False,
) -> dict[str, Any]:
    """The one evidence shape used everywhere (claims, conflicts, underwriting)."""
    assert relation in tuple(Relation) and strength in tuple(Strength)
    return {
        "artifact_key": art.key,
        "url": art.url,
        "title": art.title or art.key,
        "publisher": art.publisher,
        "kind": art.kind,
        "relation": relation,
        "strength": strength,
        "verifies": bool(verifies and relation == Relation.SUPPORTS and strength == Strength.DIRECT),
        "quote": (quote or "")[:500],
        "observed_at": iso(observed_at or art.fetched_at),
        "matcher": matcher,
        "note": note,
        "gap": gap,
    }


def gap_item(key: str, note: str, matcher: str) -> dict[str, Any]:
    """An explicit "no public evidence for this aspect" marker (not a contradiction)."""
    return {
        "artifact_key": key, "url": "", "title": "No public evidence found", "publisher": Publisher.CODE,
        "kind": "gap", "relation": Relation.CONTEXT, "strength": Strength.INDIRECT, "verifies": False,
        "quote": "", "observed_at": None, "matcher": matcher, "note": note, "gap": True,
    }


# ── helpers ──────────────────────────────────────────────────────────────────


def iso(d: datetime | date | None) -> str | None:
    return d.isoformat() if d else None


def parse_dt(v: Any) -> datetime | None:
    """ISO / GitHub / RSS-ish timestamp -> aware datetime (UTC when naive). None when unparseable."""
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=timezone.utc)
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(v, tz=timezone.utc)
    s = str(v).strip()
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        try:
            from email.utils import parsedate_to_datetime

            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def days_between(a: datetime | None, b: datetime | None) -> float | None:
    if a is None or b is None:
        return None
    return (b - a).total_seconds() / 86400.0


_VERSION_RE = re.compile(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?([A-Za-z0-9.+-]*)")


def parse_version(v: str | None) -> tuple[int, int, int, bool] | None:
    """'v0.11.2' -> (0, 11, 2, False); pre-releases ('1.0rc1', '2.0.0-beta') -> last item True."""
    if not v:
        return None
    m = _VERSION_RE.fullmatch(str(v).strip())
    if not m:
        return None
    rest = (m.group(4) or "").lower()
    pre = bool(re.search(r"(a|b|rc|alpha|beta|dev|pre|preview)\d*", rest))
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0), pre


def version_key(v: str | None) -> tuple[int, int, int, int]:
    p = parse_version(v)
    if p is None:
        return (-1, -1, -1, -1)
    return p[0], p[1], p[2], 0 if p[3] else 1


_NUM_RE = re.compile(r"(\d+(?:[.,]\d+)*)\s*(k|m|b|thousand|million|billion)?\b\+?", re.IGNORECASE)


def parse_quantity(s: str) -> float | None:
    """'10k' -> 10000, '1.2M' -> 1200000, '10,000+' -> 10000. None if no number."""
    m = _NUM_RE.search(s)
    if not m:
        return None
    raw = m.group(1)
    raw = raw.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+", raw) else raw.replace(",", ".")
    try:
        n = float(raw)
    except ValueError:
        return None
    mult = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}
    return n * mult.get((m.group(2) or "").lower(), 1)


def fmt_num(n: float | None) -> str:
    if n is None:
        return "unknown"
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 10_000:
        return f"{n / 1e3:.1f}k"
    return f"{n:,.0f}" if float(n).is_integer() else f"{n:,.2f}"


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


_STOP = frozenset(
    "the a an and or of to in on for with by is are be our we your you it its this that from as at than into "
    "can will up more most all any".split()
)


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9][a-z0-9.+#-]*", (s or "").lower()) if len(t) > 2 and t not in _STOP}


def repo_of(key: str) -> str | None:
    """'tree:org/repo' / 'file:org/repo:path' -> 'org/repo'."""
    parts = key.split(":", 2)
    return parts[1] if len(parts) >= 2 and "/" in parts[1] else None

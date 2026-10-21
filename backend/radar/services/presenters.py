"""Shared, batched converters from DB rows to API shapes (schemas/api.py).

All "why" text produced here is deterministic (built from measured signals,
metrics and score components) and labeled INFERENCE where it interprets facts.
LLM narratives live in radar.llm.narratives and are attached by detail services.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompanyMetric,
    CompanyPerson,
    EntityTopic,
    MetricSnapshot,
    Person,
    Repository,
    ScoreSnapshot,
    Signal,
    Topic,
    Watchlist,
    WatchlistItem,
)
from radar.schemas.api import (
    AntiScore,
    CompanySummary,
    EvidenceItem,
    IntelCard,
    PersonCard,
    TopicTag,
    VelocityMetric,
)
from radar.scoring.run import anti_score_from_snapshot
from radar.services.freshness import SOURCE_FAMILY, light_data_age
from radar.thesis.loader import get_thesis

NEW_DAYS = 7
# Founder-score component keys (scoring/founder.py FOUNDER_MAX), formation signals first.
PERSON_EVIDENCE_ORDER = ("formation", "technical_output", "research", "pedigree")

# Human phrasing for signal types in "Why it surfaced" (fallback: signal title).
SOURCE_GLYPH = {"github": "GH", "arxiv": "AX", "hackernews": "HN", "website": "WEB", "jobs": "JOB", "x": "X",
                "manual": "MAN", "demo": "DEMO"}


# ── score snapshots ──────────────────────────────────────────────────────────


def latest_snapshots(session: Session, ids: Iterable[uuid.UUID], day: date | None = None) -> dict[uuid.UUID, ScoreSnapshot]:
    """Most recent snapshot on/before `day` per entity (one query)."""
    ids = list(set(ids))
    if not ids:
        return {}
    day = day or clock.today()
    # DISTINCT ON walks the (entity_id, day) unique index: one row per entity, newest first.
    rows = session.scalars(
        select(ScoreSnapshot)
        .where(ScoreSnapshot.entity_id.in_(ids), ScoreSnapshot.day <= day)
        .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc())
        .distinct(ScoreSnapshot.entity_id)
    )
    return {r.entity_id: r for r in rows}


def latest_scores(session: Session, ids: Iterable[uuid.UUID], day: date | None = None) -> dict[uuid.UUID, float]:
    """Score only (no component/evidence JSON) — for ranking large id sets cheaply."""
    ids = list(set(ids))
    if not ids:
        return {}
    day = day or clock.today()
    rows = session.execute(
        select(ScoreSnapshot.entity_id, ScoreSnapshot.score)
        .where(ScoreSnapshot.entity_id.in_(ids), ScoreSnapshot.day <= day)
        .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc())
        .distinct(ScoreSnapshot.entity_id)
    )
    return {e: float(sc) for e, sc in rows}


def is_reconstructed(snap: ScoreSnapshot) -> bool:
    return bool((snap.attention or {}).get("reconstructed"))


def comparable_baseline(current: ScoreSnapshot, past: ScoreSnapshot) -> float | None:
    """The score of ``current`` to diff against ``past``, or None if the two are not comparable.

    Live and reconstructed snapshots come from different pipelines (live runs see data stamped at
    observation time that reconstruction of a past day never can), so a live score is never diffed
    against reconstructed history: the delta would measure the pipeline change, not the entity.
    When a live snapshot also carries the reconstructed score for the same day
    (``attention.reconstructed_score``), that like-for-like value is used; otherwise there is no
    delta. Snapshots from different score versions are never compared either."""
    if current.version and past.version and current.version != past.version:
        return None
    if is_reconstructed(past) and not is_reconstructed(current):
        rec = (current.attention or {}).get("reconstructed_score")
        return float(rec) if isinstance(rec, int | float) else None
    return current.score


def snapshot_delta(current: ScoreSnapshot | None, past: ScoreSnapshot | None) -> float | None:
    """current − past for two snapshots of one entity on different days, or None if not comparable."""
    if current is None or past is None or past.day >= current.day:
        return None
    base = comparable_baseline(current, past)
    return round(base - past.score, 1) if base is not None else None


def score_deltas(
    session: Session, current: dict[uuid.UUID, ScoreSnapshot], days: int, tolerance: int = 3
) -> dict[uuid.UUID, float | None]:
    """score(today) - score(nearest snapshot at/before today-days, within tolerance).

    None when there is no such snapshot or it is not comparable (see ``comparable_baseline``)."""
    if not current:
        return {}
    out: dict[uuid.UUID, float | None] = {}
    by_day: dict[date, list[uuid.UUID]] = defaultdict(list)
    for eid, snap in current.items():
        by_day[snap.day].append(eid)
    for d, ids in by_day.items():
        target = d - timedelta(days=days)
        past = latest_snapshots(session, ids, target)
        for eid in ids:
            p = past.get(eid)
            base = comparable_baseline(current[eid], p) if p and (target - p.day).days <= tolerance else None
            out[eid] = round(base - p.score, 1) if base is not None else None
    return out


def score_history(session: Session, entity_id: uuid.UUID, days: int = 60) -> list[dict[str, Any]]:
    lo = clock.today() - timedelta(days=days)
    rows = session.execute(
        select(ScoreSnapshot.day, ScoreSnapshot.score)
        .where(ScoreSnapshot.entity_id == entity_id, ScoreSnapshot.day >= lo)
        .order_by(ScoreSnapshot.day)
    )
    return [{"day": d, "score": s} for d, s in rows]


def anti_score(session: Session, snap: ScoreSnapshot | None) -> AntiScore | None:
    if snap is None:
        return None
    d7 = score_deltas(session, {snap.entity_id: snap}, 7).get(snap.entity_id)
    d30 = score_deltas(session, {snap.entity_id: snap}, 30).get(snap.entity_id)
    return anti_score_from_snapshot(snap, d7, d30)


# ── topics, watch state ──────────────────────────────────────────────────────


def topic_tags(session: Session, ids: Iterable[uuid.UUID], limit: int = 4) -> dict[uuid.UUID, list[TopicTag]]:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = session.execute(
        select(EntityTopic.entity_id, EntityTopic.score, EntityTopic.method, Topic.key, Topic.theme, Topic.label)
        .join(Topic, Topic.id == EntityTopic.topic_id)
        .where(EntityTopic.entity_id.in_(ids))
        .order_by(EntityTopic.score.desc())
    )
    out: dict[uuid.UUID, list[TopicTag]] = defaultdict(list)
    seen: dict[uuid.UUID, set[str]] = defaultdict(set)
    for eid, score, method, key, theme, label in rows:
        if key in seen[eid] or len(out[eid]) >= limit:
            continue
        seen[eid].add(key)
        out[eid].append(TopicTag(key=key, theme=theme, label=label, score=round(score, 3), method=method))
    return out


def watched_ids(session: Session) -> dict[uuid.UUID, uuid.UUID]:
    """entity_id -> watchlist item id."""
    rows = session.execute(select(WatchlistItem.entity_id, WatchlistItem.id).join(Watchlist))
    return {e: i for e, i in rows if e is not None}


def theme_label(theme: str) -> str:
    t = get_thesis().themes.get(theme)
    return t.label if t else theme.replace("_", " ").title()


# ── labels ───────────────────────────────────────────────────────────────────


def kind_label(c: Company, tags: Sequence[TopicTag]) -> str:
    domain = theme_label(tags[0].theme).upper() if tags else ""
    if c.kind == "stealth":
        base = f"STEALTH {domain} COMPANY" if domain else "STEALTH COMPANY"
    elif c.kind == "project":
        base = f"{domain} OPEN-SOURCE PROJECT" if domain else "OPEN-SOURCE PROJECT"
    elif c.kind == "lab":
        base = f"{domain} RESEARCH LAB" if domain else "RESEARCH LAB"
    else:
        base = f"{domain} COMPANY" if domain else "COMPANY"
    return base.replace("  ", " ").strip()


def person_kind_label(p: Person, link: CompanyPerson | None) -> str:
    """'FOUNDER' only when a CompanyPerson founder relation backs it; otherwise a neutral label —
    a high founder score or a missing company field is not evidence that someone founded anything."""
    if link is not None and link.is_founder:
        return "FOUNDER"
    return "PERSON TO WATCH"


def data_note(is_demo: bool) -> str:
    if is_demo:
        return "DEMO DATA — fictional example"
    a = clock.as_of()
    return f"Real public data captured {a:%b %-d, %Y}" if a else "Live public data"


def _new_projects(snap: ScoreSnapshot | None) -> list[str]:
    """Own new repositories the Founder Score's formation component recorded (``new_project`` events)."""
    if snap is None:
        return []
    raw = (((snap.components or {}).get("formation") or {}).get("raw") or {})
    return [str(x) for x in (raw.get("new_projects") or []) if x]


def talent_line(p: Person, snap: ScoreSnapshot | None = None) -> str | None:
    """'Contributor to google-deepmind/mujoco · now building ada/tactile-sim' from verified lab commits
    (``Person.attrs['lab_contributions']``) and a recorded new project. Never claims employment; a new
    repository is not a company."""
    from radar.scoring.extract import person_lab_contributions

    labs = person_lab_contributions(p)
    if not labs:
        return None
    line = f"Contributor to {labs[0].full_name}"
    projects = _new_projects(snap)
    if projects:
        line += f" \u00b7 now building {projects[0]}"
    return line


def headline(p: Person, snap: ScoreSnapshot | None = None) -> str | None:
    talent = talent_line(p, snap)
    if talent:
        return talent
    h = (p.attrs or {}).get("headline")
    if h:
        return h
    company = clean_label(p.current_company)
    if p.current_role and company:
        return f"{p.current_role}, {company}"
    if company:
        return company
    prior = [a for a in map(clean_label, p.prior_affiliations or []) if a]
    if prior:
        return f"Previously {prior[-1]}"
    return (p.bio or "")[:90] or None


def avatar(p: Person) -> str | None:
    if p.is_demo:
        return None
    a = (p.attrs or {}).get("avatar_url")
    if a:
        return a
    return f"https://github.com/{p.github_handle}.png?size=96" if p.github_handle else None


# ── display hygiene ──────────────────────────────────────────────────────────

DISPLAY_NAME_MAX = 40
# ": ", " | ", " — ", " – " separate a name from a tagline ("Mistral AI: frontier models"); a plain
# " - " only does when the whole thing is too long to be a name ("TUM - Autonomous Vehicle Systems
# Lab" is a name; "V-Modal AI - Visual Memory Layer: Find, recall…" is not).
_TAGLINE_SPLIT = re.compile(r"\s+[–—]\s+|:\s+|\s*\|\s*")
_HYPHEN_SPLIT = re.compile(r"\s+-\s+")
_SLUG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*/[A-Za-z0-9_.\-]+$")  # owner/repo
SELF_REPORTED_NOTE = "Self-reported on GitHub profile"
HANDLE_ONLY_NOTE = "handle only"


@dataclass(frozen=True)
class DisplayName:
    name: str  # what to show
    raw: str  # as recorded
    note: str | None = None  # why it differs ("cleaned from a repository name / tagline", "handle only")

    @property
    def raw_if_changed(self) -> str | None:
        return self.raw if self.raw != self.name else None


def _truncate(text: str, limit: int = DISPLAY_NAME_MAX) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;-–—")
    return (cut or text[: limit - 1]) + "…"


def clean_company_name(name: str | None, *, github_org: str | None = None, slug: str | None = None
                       ) -> tuple[str, str | None]:
    """(display name, note). Strips an ``owner/`` prefix from repo slugs and a tagline after
    ' - ' / ':' / '|'; trims to 40 characters; falls back to the GitHub org (title-cased) or slug
    only when the name is missing."""
    raw = (name or "").strip()
    if not raw:
        base = github_org or slug or "Unnamed"
        return base.replace("-", " ").replace("_", " ").title(), "No name on record; derived from GitHub org"
    out, note = raw, None
    if _SLUG_RE.match(out):
        out, note = out.rsplit("/", 1)[1], "Repository name"
    splitter = _TAGLINE_SPLIT
    if len(out) > DISPLAY_NAME_MAX:
        splitter = re.compile(f"{_TAGLINE_SPLIT.pattern}|{_HYPHEN_SPLIT.pattern}")
    parts = [p.strip() for p in splitter.split(out) if p and p.strip()]
    if len(parts) > 1 and len(parts[0]) >= 2:
        out, note = parts[0], "Name shortened from a tagline"
    if len(out) > DISPLAY_NAME_MAX:
        out, note = _truncate(out), note or "Name shortened"
    return out, note


def is_handle_only(name: str | None, *handles: str | None) -> bool:
    n = (name or "").strip().lstrip("@").lower()
    return not n or any(h and n == h.strip().lstrip("@").lower() for h in handles)


def display_name(entity: Any) -> DisplayName:
    """Clean display name for a Company or Person; the raw name is always kept.

    People recorded under nothing but a handle are shown as ``@handle`` with note "handle only"."""
    raw = (getattr(entity, "name", None) or "").strip()
    if isinstance(entity, Person):
        handle = entity.github_handle or entity.x_handle
        if handle and is_handle_only(raw, entity.github_handle, entity.x_handle):
            return DisplayName(name=f"@{handle.lstrip('@')}", raw=raw, note=HANDLE_ONLY_NOTE)
        return DisplayName(name=_truncate(raw, 60) if raw else entity.slug, raw=raw)
    name, note = clean_company_name(raw, github_org=getattr(entity, "github_org", None),
                                    slug=getattr(entity, "slug", None))
    return DisplayName(name=name, raw=raw, note=note)


_BIBTEX_MARKS = {'"': "\u0308", "'": "\u0301", "`": "\u0300", "^": "\u0302", "~": "\u0303", "=": "\u0304",
                 ".": "\u0307", "u": "\u0306", "v": "\u030c", "H": "\u030b", "c": "\u0327"}
_BIBTEX_ACCENT = re.compile(r"\{?\\([\"'`^~=.uvHc])\s*\{?([A-Za-z])\}?\}?")


def clean_label(value: str | None) -> str | None:
    """Free-text affiliation / company labels from profiles: drop BibTeX escapes and braces,
    a leading '@', and values with no letters or digits ("✖️", "-")."""
    if not value:
        return None
    v = _BIBTEX_ACCENT.sub(lambda m: m.group(2) + _BIBTEX_MARKS.get(m.group(1), ""), value)  # {\"o} -> ö
    v = v.replace("{", "").replace("}", "").replace("\\", "")
    v = unicodedata.normalize("NFC", v).strip().lstrip("@").strip()
    v = re.sub(r"\s+", " ", v)
    if not any(ch.isalnum() for ch in v):
        return None
    return v[:120]


# ── velocity ─────────────────────────────────────────────────────────────────

# key -> (label, unit noun). Values are per day (scoring/velocity.py); accelerations are the change
# between the previous and the latest 7-day rate.
VELOCITY_LABELS: dict[str, tuple[str, str]] = {
    "star_velocity_7d": ("Star velocity (7d)", "stars"),
    "star_velocity_30d": ("Star velocity (30d)", "stars"),
    "star_velocity_prev_7d": ("Star velocity (previous 7d)", "stars"),
    "star_acceleration": ("Star acceleration", "stars"),
    "commit_velocity_7d": ("Commit velocity (7d)", "commits"),
    "commit_velocity_30d": ("Commit velocity (30d)", "commits"),
    "commit_velocity_prev_7d": ("Commit velocity (previous 7d)", "commits"),
    "commit_acceleration": ("Commit acceleration", "commits"),
    "contributor_velocity_30d": ("New contributors (30d)", "contributors"),
    "release_velocity_90d": ("Release cadence (90d)", "releases"),
    "issue_velocity_30d": ("Open issues, net change (30d)", "issues"),
}
_BASES = ("observed", "backfill", "insufficient_history")


def _num(v: float) -> str:
    a = abs(v)
    if a >= 100:
        return f"{v:,.0f}"
    if a >= 10:
        return f"{v:.0f}"
    return f"{v:.1f}".rstrip("0").rstrip(".") if a >= 1 else f"{v:.2f}".rstrip("0").rstrip(".")


def _rate(v: float, noun: str) -> str:
    """Per-day rate in readable units: per day when |v| ≥ 1/day, else per week."""
    if v == 0:
        return f"0 {noun}/day"
    if abs(v) >= 1:
        return f"{_num(v)} {noun}/day"
    return f"{_num(v * 7)} {noun}/week"


def _is_num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def velocity_metrics(metrics: dict[str, Any] | None) -> list[VelocityMetric]:
    """API view of ``CompanyMetric.metrics['velocity']`` (tolerates a missing / partial dict).

    ``insufficient_history`` values are shown as such, never as zero; backfilled values are marked
    reconstructed; company sums over only some repositories are noted as lower bounds."""
    vel = (metrics or {}).get("velocity")
    if not isinstance(vel, dict):
        return []
    extra = sorted(k for k in vel if k not in VELOCITY_LABELS and ("velocity" in k or "acceleration" in k)
                   and not k.endswith(("_basis", "_text")) and (vel[k] is None or _is_num(vel[k])))
    coverage = vel.get("coverage") if isinstance(vel.get("coverage"), dict) else {}
    repos = vel.get("repos") if _is_num(vel.get("repos")) else None
    out = []
    for k in [k for k in VELOCITY_LABELS if k in vel] + extra:
        v = vel.get(k)
        value = float(v) if _is_num(v) else None
        basis = vel.get(f"{k}_basis")
        if basis not in _BASES or value is None:
            basis = "insufficient_history" if value is None else "observed"
        label, noun = VELOCITY_LABELS.get(k, (k.replace("_", " ").capitalize(), ""))
        unit = f"{noun}/day" if noun else None
        if value is None:
            human = "Insufficient observed history"
        elif k.endswith("_acceleration"):
            fam = k.removesuffix("_acceleration")
            prev, cur = vel.get(f"{fam}_velocity_prev_7d"), vel.get(f"{fam}_velocity_7d")
            if isinstance(vel.get(f"{k}_text"), str) and vel[f"{k}_text"]:
                human = vel[f"{k}_text"]
            elif _is_num(prev) and _is_num(cur) and noun:
                human = f"{_num(prev)} → {_num(cur)} {noun}/day"
            else:
                human = f"{'+' if value > 0 else ''}{_num(value)} {noun}/day".rstrip()
        else:
            human = _rate(value, noun) if noun else _num(value)
        if basis == "backfill":
            human += " (reconstructed)"
        n = coverage.get(k)
        note = (f"Sum over {int(n)} of {int(repos)} repositories (lower bound)"
                if value is not None and _is_num(n) and repos and 0 < n < repos else None)
        out.append(VelocityMetric(key=k, label=label, value=value, unit=unit, basis=basis, human=human, note=note))
    return out


# ── evidence & why ───────────────────────────────────────────────────────────


def evidence_from_signal(s: Signal) -> EvidenceItem:
    return EvidenceItem(
        signal_id=str(s.id),
        title=s.title,
        detail=s.description,
        source_type=s.source_type,
        source_url=s.source_url,
        event_at=s.event_at,
    )


def _evidence_rank(e: dict[str, Any]) -> tuple:
    return (e.get("weight") or 0, e.get("event_at") or "")


def component_evidence(comp: dict[str, Any] | None) -> list[dict[str, Any]]:
    """A score component's evidence, or nothing when the component contributed nothing (value 0 /
    unmeasured) — evidence is only cited for inputs that actually moved the score."""
    if not comp or not (comp.get("value") or 0) > 0:
        return []
    return list(comp.get("evidence") or [])


def why_surfaced(snap: ScoreSnapshot | None, metrics: dict[str, Any] | None, limit: int = 6) -> list[EvidenceItem]:
    """Deterministic 'Why it surfaced': measured deltas first, then the strongest evidence signals."""
    items: list[EvidenceItem] = []
    m = metrics or {}
    if m.get("stars_30d") and m["stars_30d"] >= 20:
        prev = m.get("stars_prev_28d")
        rate = ""
        if prev and prev > 0 and m.get("stars_7d") is not None:
            ratio = (m["stars_7d"] / 7) / (prev / 28)
            if ratio >= 1.5:
                rate = f"; 7-day rate {ratio:.1f}× the prior 4 weeks"
        items.append(EvidenceItem(title=f"GitHub stars +{int(m['stars_30d']):,} in 30 days{rate}",
                                  source_type="github", detail="Computed from daily star history"))
    c4, p4 = m.get("commits_4w"), m.get("commits_prev_4w")
    if c4 and p4 and p4 > 0 and c4 / p4 >= 1.5 and c4 >= 15:
        items.append(EvidenceItem(title=f"Commit activity +{(c4 / p4 - 1) * 100:.0f}% vs the prior 4 weeks",
                                  detail=f"{int(c4)} commits in 4 weeks vs {int(p4)}", source_type="github"))
    if m.get("new_contributors_30d") and m["new_contributors_30d"] >= 2:
        items.append(EvidenceItem(title=f"{int(m['new_contributors_30d'])} new contributors in 30 days",
                                  source_type="github"))
    if snap is not None:
        col = snap.collision or {}
        if col.get("families") and len(col["families"]) >= 3:
            items.append(EvidenceItem(title=f"Signal collision: {col.get('summary')}", source_type="system",
                                      detail=", ".join(col["families"])))
        seen_titles = {i.title for i in items}
        pool: list[dict[str, Any]] = []
        for comp in (snap.components or {}).values():
            pool.extend(component_evidence(comp))
        pool.extend(col.get("evidence") or [])
        pool.sort(key=_evidence_rank, reverse=True)
        seen_ids: set[str] = set()
        for e in pool:
            sid = e.get("signal_id")
            if (sid and sid in seen_ids) or e.get("title") in seen_titles:
                continue
            if e.get("title", "").startswith(("GitHub stars +", "+")) and any(
                i.title.startswith("GitHub stars") for i in items
            ):
                continue
            seen_ids.add(sid or "")
            seen_titles.add(e.get("title"))
            items.append(EvidenceItem.model_validate(e))
            if len(items) >= limit:
                break
        att = snap.attention or {}
        if att.get("flagged") and len(items) < limit + 1:
            items.append(EvidenceItem(
                title=f"Little conventional attention (attention percentile {att.get('attention', 0):.0f} "
                      f"vs technical {att.get('technical', 0):.0f})",
                source_type="system"))
    return items[: limit + 1]


def why_anti(c: Company, tags: Sequence[TopicTag], snap: ScoreSnapshot | None) -> list[str]:
    """Deterministic 'Why Anti Fund might care' — thesis + stage + score-structure reasoning."""
    lines: list[str] = []
    if tags:
        labels = []
        for t in tags[:3]:
            lab = f"{theme_label(t.theme)} · {t.label}"
            if lab not in labels:
                labels.append(lab)
        lines.append(" + ".join(labels[:2]) + ".")
    comps = (snap.components if snap else {}) or {}
    fs = comps.get("founder_signal", {})
    if fs and fs.get("max") and fs.get("value", 0) / fs["max"] >= 0.6:
        # Founder Signal measures the linked people's technical record; it does not establish that they founded it.
        lines.append("Strong technical team linked to it.")
    if c.stage in ("formation", "pre_seed"):
        lines.append("Formation-stage signal." if c.stage == "formation" else "Pre-seed stage (inferred).")
    att = (snap.attention if snap else {}) or {}
    if att.get("flagged"):
        lines.append("Attention appears significantly behind technical momentum.")
    col = (snap.collision if snap else {}) or {}
    if col.get("score", 0) >= 0.7 and len(col.get("families") or []) >= 3:
        lines.append(f"Independent signals are converging ({len(col['families'])} source families).")
    if not lines and comps.get("technical_momentum", {}).get("value", 0) >= 15:
        lines.append("Technical momentum in the top tier of tracked entities.")
    return lines


# ── trust: confidence, why-now sentence, recent change ───────────────────────

CONF_HIGH_FAMILIES, CONF_HIGH_EVIDENCE = 3, 4
CONF_MEDIUM_FAMILIES = 2
CONF_HIGH_MIN_VERIFIED = 0.5  # HIGH also needs at least half the evidence directly cited (signal / URL)
WHY_NOW_CLAUSES = 3


def confidence_for(families: Iterable[str] | int, evidence_count: int, verified_share: float | None = None) -> str:
    """Card confidence from evidence breadth (deterministic):

    * HIGH CONFIDENCE — >= 3 independent source families and >= 4 evidence items (and, when known,
      at least half of the evidence directly cited);
    * MEDIUM CONFIDENCE — >= 2 independent families;
    * EARLY SIGNAL — otherwise (one family, or nothing dated/cited)."""
    n = families if isinstance(families, int) else len(set(families))
    cited_ok = verified_share is None or verified_share >= CONF_HIGH_MIN_VERIFIED
    if n >= CONF_HIGH_FAMILIES and evidence_count >= CONF_HIGH_EVIDENCE and cited_ok:
        return "HIGH CONFIDENCE"
    if n >= CONF_MEDIUM_FAMILIES:
        return "MEDIUM CONFIDENCE"
    return "EARLY SIGNAL"


def ago_phrase(at: datetime, now: datetime | None = None) -> str:
    """'today' / 'yesterday' / '5 days ago' / '3 weeks ago' / '4 months ago' on the data clock."""
    now = now or clock.now()
    if at.tzinfo is None:
        at = at.replace(tzinfo=now.tzinfo)
    days = (now.date() - at.date()).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 21:
        return f"{days} days ago"
    if days < 70:
        return f"{round(days / 7)} weeks ago"
    return f"{round(days / 30)} months ago"


def _clause(e: EvidenceItem, now: datetime, dated: bool = True) -> str:
    title = (e.title or "").strip().rstrip(".")
    if len(title) > 90:
        title = title[:89].rsplit(" ", 1)[0] + "…"
    return f"{title} ({ago_phrase(e.event_at, now)})" if dated and e.event_at else title


def _evidence_family(e: EvidenceItem) -> str | None:
    return SOURCE_FAMILY.get(e.source_type or "")


_SLUG_IN_TEXT = re.compile(r"\b[\w.\-]+/[\w.\-]+\b")


def _same_story(a: EvidenceItem, b: EvidenceItem) -> bool:
    """Two evidence lines telling one story: one title contains the other ('FDE-Gym …' / 'Code for arXiv
    paper: FDE-Gym …'), or both name the same owner/repo on the same day."""
    ta, tb = (a.title or "").lower().strip(), (b.title or "").lower().strip()
    if ta and tb and (ta in tb or tb in ta):
        return True
    if a.event_at and b.event_at and _aware(a.event_at).date() == _aware(b.event_at).date():
        return bool(set(_SLUG_IN_TEXT.findall(ta)) & set(_SLUG_IN_TEXT.findall(tb)))
    return False


def _dedupe(items: Iterable[EvidenceItem]) -> list[EvidenceItem]:
    out: list[EvidenceItem] = []
    seen = set()
    for e in items:
        k = e.signal_id or (e.title, e.source_url)
        if k in seen:
            continue
        seen.add(k)
        out.append(e)
    return out


@dataclass(frozen=True)
class CardTrust:
    confidence: str
    why_now_sentence: str
    recent_change: str | None
    sources_count: int
    families: tuple[str, ...]


# Signals dated when *observed* rather than when the change happened (metric restatements, website
# language): they are measurements with their own window in the title, never "dated changes".
OBSERVATION_DATED_TYPES = frozenset({"formation_language"})


def observation_dated_ids(session: Session, evidence: Iterable[EvidenceItem]) -> set[str]:
    """Signal ids (as str) among ``evidence`` whose type is observation-dated (one query)."""
    from radar.scoring.features import MEASUREMENT_SIGNAL_TYPES

    ids = []
    for e in evidence:
        if not e.signal_id:
            continue
        try:
            ids.append(uuid.UUID(e.signal_id))
        except ValueError:
            continue
    if not ids:
        return set()
    types = MEASUREMENT_SIGNAL_TYPES | OBSERVATION_DATED_TYPES
    rows = session.execute(select(Signal.id, Signal.signal_type).where(Signal.id.in_(ids)))
    return {str(i) for i, t in rows if t in types}


def card_trust(evidence: Iterable[EvidenceItem], now: datetime | None = None,
               observed: set[str] | frozenset[str] = frozenset()) -> CardTrust:
    """Confidence / why-now / recent change from a card's evidence (why_surfaced + collision evidence).

    Only evidence from a real source family counts (system lines like 'Signal collision: …' do not).
    ``observed`` = signal ids dated at observation time (``observation_dated_ids``): they are treated as
    measurements (undated). The why-now sentence leads with the strongest measurement, then the strongest
    *dated* events (weight, then recency), at most three clauses, one per story. The recent change is the
    newest dated event. Nothing is invented: no evidence -> '' / None."""
    now = now or clock.now()
    future = now + timedelta(days=1)  # evidence dated after the as-of time is not evidence for it
    items = [e for e in _dedupe(evidence)
             if _evidence_family(e) and (e.event_at is None or _aware(e.event_at) <= future)]
    families = tuple(sorted({f for e in items if (f := _evidence_family(e))}))
    cited = [e for e in items if e.signal_id or e.source_url]
    share = len(cited) / len(items) if items else None
    is_event = [e.event_at is not None and (e.signal_id or "") not in observed for e in items]
    events = [e for e, ev in zip(items, is_event, strict=True) if ev]
    # Measurements with a number ("+616 stars in 30 days") lead; other undated facts follow.
    measures = sorted((e for e, ev in zip(items, is_event, strict=True) if not ev),
                      key=lambda e: not re.search(r"\d", e.title or ""))
    picked: list[tuple[EvidenceItem, bool]] = []
    for e, dated in [*((m, False) for m in measures[:1]),
                     *((e, True) for e in sorted(events, key=lambda e: (e.weight or 0.0, _aware(e.event_at)),
                                                 reverse=True))]:
        if len(picked) >= WHY_NOW_CLAUSES:
            break
        if not any(_same_story(e, p) for p, _ in picked):
            picked.append((e, dated))
    head = [p for p in picked if not p[1]]
    tail = sorted((p for p in picked if p[1]), key=lambda p: _aware(p[0].event_at), reverse=True)
    sentence = "; ".join(_clause(e, now, dated) for e, dated in head + tail)
    newest = max(events, key=lambda e: _aware(e.event_at), default=None)
    return CardTrust(
        confidence=confidence_for(families, len(items), share),
        why_now_sentence=sentence,
        recent_change=_clause(newest, now) if newest else None,
        sources_count=len(families),
        families=families,
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _with_gap_points(ascore: AntiScore | None, snap: ScoreSnapshot | None) -> AntiScore | None:
    """Fill AttentionGap.gap_points from the stored component for snapshots that predate it."""
    gap = ascore.attention_gap if ascore else None
    if gap is None or gap.gap_points is not None or gap.attention_score is None or snap is None:
        return ascore
    comp = (snap.components or {}).get("attention_gap") or {}
    if isinstance(comp.get("value"), int | float):
        gap.gap_points = round(float(comp["value"]), 2)
    return ascore


def _set_trust(session: Session, cards: list[IntelCard]) -> None:
    """Fill confidence / why_now_sentence / recent_change / sources_count on cards (one signal-type query)."""
    allev = [e for c in cards for e in [*c.why_surfaced, *(c.collision.evidence if c.collision else [])]]
    observed = observation_dated_ids(session, allev)
    for c in cards:
        t = card_trust([*c.why_surfaced, *(c.collision.evidence if c.collision else [])], observed=observed)
        c.confidence, c.why_now_sentence = t.confidence, t.why_now_sentence
        c.recent_change, c.sources_count = t.recent_change, t.sources_count


# ── sparklines ───────────────────────────────────────────────────────────────


def star_sparklines(session: Session, company_ids: Sequence[uuid.UUID], days: int = 30) -> dict[uuid.UUID, list[float]]:
    """Daily total stars across a company's repos, last `days` days (carry-forward fill)."""
    if not company_ids:
        return {}
    lo = clock.today() - timedelta(days=days)
    rows = session.execute(
        select(Repository.company_id, MetricSnapshot.entity_id, MetricSnapshot.day, MetricSnapshot.value)
        .join(MetricSnapshot, MetricSnapshot.entity_id == Repository.id)
        .where(Repository.company_id.in_(list(company_ids)), MetricSnapshot.metric == "stars",
               MetricSnapshot.day >= lo - timedelta(days=14))
        .order_by(MetricSnapshot.day)
    )
    per_repo: dict[tuple[uuid.UUID, uuid.UUID], dict[date, float]] = defaultdict(dict)
    for cid, rid, d, v in rows:
        per_repo[(cid, rid)][d] = v
    out: dict[uuid.UUID, list[float]] = defaultdict(lambda: [0.0] * (days + 1))
    for (cid, _rid), series in per_repo.items():
        last = None
        days_sorted = sorted(series)
        idx = 0
        vals = out[cid]
        for i in range(days + 1):
            d = lo + timedelta(days=i)
            while idx < len(days_sorted) and days_sorted[idx] <= d:
                last = series[days_sorted[idx]]
                idx += 1
            vals[i] += last or 0.0
    return {k: v for k, v in out.items() if any(v)}


def person_sparklines(session: Session, person_ids: Sequence[uuid.UUID], days: int = 30) -> dict[uuid.UUID, list[float]]:
    """Score history as the founder sparkline."""
    if not person_ids:
        return {}
    lo = clock.today() - timedelta(days=days)
    rows = session.execute(
        select(ScoreSnapshot.entity_id, ScoreSnapshot.day, ScoreSnapshot.score)
        .where(ScoreSnapshot.entity_id.in_(list(person_ids)), ScoreSnapshot.day >= lo)
        .order_by(ScoreSnapshot.day)
    )
    out: dict[uuid.UUID, list[float]] = defaultdict(list)
    for eid, _d, s in rows:
        out[eid].append(float(s))
    return {k: v for k, v in out.items() if len(v) > 1}


# ── summaries & cards ────────────────────────────────────────────────────────


def company_metrics(session: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    if not ids:
        return {}
    return dict(session.execute(select(CompanyMetric.company_id, CompanyMetric.metrics)
                                .where(CompanyMetric.company_id.in_(list(ids)))).tuples().all())


def one_liner(c: Company) -> str | None:
    d = (c.description or "").strip()
    if not d:
        return None
    first = d.split(". ")[0].strip()
    return first if first.endswith(".") or len(first) < 40 else first + "."


def company_summaries(session: Session, companies: Sequence[Company]) -> list[CompanySummary]:
    ids = [c.id for c in companies]
    snaps = latest_snapshots(session, ids)
    d7 = score_deltas(session, snaps, 7)
    tags = topic_tags(session, ids)
    mets = company_metrics(session, ids)
    watched = watched_ids(session)
    out = []
    for c in companies:
        snap, m = snaps.get(c.id), mets.get(c.id, {})
        dn = display_name(c)
        out.append(CompanySummary(
            id=str(c.id), slug=c.slug, name=dn.name, raw_name=dn.raw_if_changed, name_note=dn.note,
            one_liner=one_liner(c), kind=c.kind, stage=c.stage,
            score=round(snap.score) if snap else None, delta_7d=d7.get(c.id),
            thesis_tags=tags.get(c.id, []),
            attention_gap=(snap.attention or {}).get("gap") if snap else None,
            collision=(snap.collision or {}).get("score") if snap else None,
            stars_30d=m.get("stars_30d"), first_seen_at=c.first_seen_at, website=c.website,
            github_org=c.github_org, is_demo=c.is_demo, watched=c.id in watched,
        ))
    return out


def founder_links(session: Session, person_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, CompanyPerson]:
    if not person_ids:
        return {}
    rows = session.scalars(
        select(CompanyPerson).where(CompanyPerson.person_id.in_(list(person_ids)))
        .order_by(CompanyPerson.is_founder.desc(), CompanyPerson.confidence.desc())
    )
    out: dict[uuid.UUID, CompanyPerson] = {}
    for cp in rows:
        out.setdefault(cp.person_id, cp)
    return out


def person_why_now(p: Person, snap: ScoreSnapshot | None, limit: int = 4) -> list[str]:
    """Deterministic founder 'why now' bullets from score evidence (formation signals first)."""
    if snap is None:
        return []
    from radar.scoring.extract import person_lab_contributions

    comps = snap.components or {}
    order = [*PERSON_EVIDENCE_ORDER, "thesis_fit", "attention_gap"]
    bullets: list[str] = []
    seen: set[str] = set()
    # Talent pool: the new project (possible formation) first, then the verified technical history.
    labs = person_lab_contributions(p)
    projects = _new_projects(snap)
    if labs and projects:
        bullets.append(f"Now building {projects[0]} \u2014 new repository of their own (possible formation)")
    if labs:
        bullets.append(labs[0].phrase(verb=False))
    seen.update(bullets)
    for key in order + [k for k in comps if k not in order]:
        for e in component_evidence(comps.get(key)):
            t = e.get("title")
            if t and t not in seen:
                seen.add(t)
                bullets.append(t)
            if len(bullets) >= limit:
                return bullets
    pedigree = (comps.get("pedigree") or {}).get("explanation")
    if pedigree and len(bullets) < limit and "no" not in pedigree.lower()[:3]:
        bullets.append(pedigree)
    return bullets[:limit]


def person_cards(session: Session, people: Sequence[Person]) -> list[PersonCard]:
    ids = [p.id for p in people]
    snaps = latest_snapshots(session, ids)
    d7 = score_deltas(session, snaps, 7)
    links = founder_links(session, ids)
    out = []
    for p in people:
        snap, cp = snaps.get(p.id), links.get(p.id)
        dn = display_name(p)
        out.append(PersonCard(
            id=str(p.id), slug=p.slug, name=dn.name, raw_name=dn.raw_if_changed, name_note=dn.note,
            headline=headline(p, snap), current_company=clean_label(p.current_company),
            current_role=p.current_role or (cp.role if cp else None),
            prior_affiliations=[a for a in map(clean_label, p.prior_affiliations or []) if a],
            domains=p.domains or [], github_handle=p.github_handle, x_handle=p.x_handle,
            personal_site=p.personal_site, avatar_url=avatar(p),
            founder_score=round(snap.score) if snap else None, delta_7d=d7.get(p.id),
            why_now=person_why_now(p, snap), is_founder=bool(cp and cp.is_founder),
            role=cp.role if cp else None, is_demo=p.is_demo, first_seen_at=p.first_seen_at,
        ))
    return out


def company_cards(session: Session, companies: Sequence[Company], start_rank: int = 1) -> list[IntelCard]:
    ids = [c.id for c in companies]
    snaps = latest_snapshots(session, ids)
    d7, d30 = score_deltas(session, snaps, 7), score_deltas(session, snaps, 30)
    tags = topic_tags(session, ids)
    mets = company_metrics(session, ids)
    sparks = star_sparklines(session, ids)
    watched = watched_ids(session)
    cutoff = clock.now() - timedelta(days=NEW_DAYS)
    cards = []
    for i, c in enumerate(companies):
        snap = snaps.get(c.id)
        ascore = _with_gap_points(anti_score_from_snapshot(snap) if snap else None, snap)
        dn = display_name(c)
        why = why_surfaced(snap, mets.get(c.id))
        cards.append(IntelCard(
            rank=start_rank + i, entity_type="company", id=str(c.id), slug=c.slug, name=dn.name,
            raw_name=dn.raw_if_changed, name_note=dn.note,
            kind_label=kind_label(c, tags.get(c.id, [])), one_liner=one_liner(c),
            score=round(snap.score) if snap else 0, delta_7d=d7.get(c.id), delta_30d=d30.get(c.id),
            thesis_tags=tags.get(c.id, []), why_surfaced=why,
            why_anti=why_anti(c, tags.get(c.id, []), snap), why_anti_epistemic="INFERENCE",
            attention_gap=ascore.attention_gap if ascore else None,
            collision=ascore.collision if ascore else None,
            sparkline=sparks.get(c.id, []), sparkline_label="GitHub stars · 30d" if c.id in sparks else None,
            first_seen_at=c.first_seen_at, is_new=bool(c.first_seen_at and c.first_seen_at >= cutoff),
            watched=c.id in watched, is_demo=c.is_demo, data_note=data_note(c.is_demo),
            data_age=light_data_age(c),
        ))
    _set_trust(session, cards)
    return cards


def founder_intel_cards(session: Session, people: Sequence[Person], start_rank: int = 1) -> list[IntelCard]:
    ids = [p.id for p in people]
    snaps = latest_snapshots(session, ids)
    d7, d30 = score_deltas(session, snaps, 7), score_deltas(session, snaps, 30)
    tags = topic_tags(session, ids)
    sparks = person_sparklines(session, ids)
    links = founder_links(session, ids)
    watched = watched_ids(session)
    cutoff = clock.now() - timedelta(days=NEW_DAYS)
    cards = []
    for i, p in enumerate(people):
        snap = snaps.get(p.id)
        ascore = _with_gap_points(anti_score_from_snapshot(snap) if snap else None, snap)
        why = [EvidenceItem.model_validate(e) for e in _person_evidence(snap)]
        domains = ", ".join(theme_label(d) for d in (p.domains or [])[:2])
        lines = []
        prior = [a for a in map(clean_label, p.prior_affiliations or []) if a]
        if clean_label(p.current_company) is None and prior:
            lines.append(f"No current company listed — previously {prior[-1]}.")
        if domains:
            lines.append(f"Technical domain: {domains}.")
        if ascore and ascore.attention_gap and ascore.attention_gap.flagged:
            lines.append("Technical output well ahead of public following.")
        dn = display_name(p)
        cards.append(IntelCard(
            rank=start_rank + i, entity_type="person", id=str(p.id), slug=p.slug, name=dn.name,
            raw_name=dn.raw_if_changed, name_note=dn.note,
            kind_label=person_kind_label(p, links.get(p.id)),
            one_liner=headline(p, snap), score=round(snap.score) if snap else 0, delta_7d=d7.get(p.id),
            delta_30d=d30.get(p.id), thesis_tags=tags.get(p.id, []), why_surfaced=why, why_anti=lines,
            attention_gap=ascore.attention_gap if ascore else None, collision=ascore.collision if ascore else None,
            sparkline=sparks.get(p.id, []), sparkline_label="Founder score · 30d" if p.id in sparks else None,
            first_seen_at=p.first_seen_at, is_new=bool(p.first_seen_at and p.first_seen_at >= cutoff),
            watched=p.id in watched, is_demo=p.is_demo, data_note=data_note(p.is_demo),
            data_age=light_data_age(p),
        ))
    _set_trust(session, cards)
    return cards


def _person_evidence(snap: ScoreSnapshot | None, limit: int = 5) -> list[dict[str, Any]]:
    if snap is None:
        return []
    comps = snap.components or {}
    order = list(PERSON_EVIDENCE_ORDER)
    out, seen = [], set()
    for key in order + [k for k in comps if k not in order]:
        for e in component_evidence(comps.get(key)):
            if e.get("title") in seen:
                continue
            seen.add(e.get("title"))
            out.append(e)
            if len(out) >= limit:
                return out
    return out


def ranked_companies(session: Session, *, limit: int = 20, include_demo: bool = True,
                     kinds: Sequence[str] | None = None) -> list[Company]:
    """Companies ordered by latest score (today's snapshots)."""
    day = clock.today()
    q = (
        select(Company)
        .join(ScoreSnapshot, and_(ScoreSnapshot.entity_id == Company.id, ScoreSnapshot.day == day))
        .where(Company.dismissed.is_(False))
        .order_by(ScoreSnapshot.score.desc())
        .limit(limit)
    )
    if not include_demo:
        q = q.where(Company.is_demo.is_(False))
    if kinds:
        q = q.where(Company.kind.in_(list(kinds)))
    return list(session.scalars(q))


def ranked_people(session: Session, *, limit: int = 20, include_demo: bool = True) -> list[Person]:
    day = clock.today()
    q = (
        select(Person)
        .join(ScoreSnapshot, and_(ScoreSnapshot.entity_id == Person.id, ScoreSnapshot.day == day))
        .where(Person.dismissed.is_(False))
        .order_by(ScoreSnapshot.score.desc())
        .limit(limit)
    )
    if not include_demo:
        q = q.where(Person.is_demo.is_(False))
    return list(session.scalars(q))


def resolve_entity(session: Session, model, id_or_slug: str):
    try:
        uid = uuid.UUID(str(id_or_slug))
        row = session.get(model, uid)
        if row:
            return row
    except ValueError:
        pass
    return session.scalar(select(model).where(model.slug == id_or_slug))


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None

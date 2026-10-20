"""Home (GET /api/home): the under-30-second read.

One response carries the count strip, a short WHAT CHANGED feed and the top few items of each
section (formations, people to meet, accelerating companies, strange signals). It composes the
existing builders — ``services.formation``, ``services.meet``, ``services.feed`` and the stored
score snapshots — and adds no new scoring.

Counts (all under the demo filter and, when set, the ``new`` window)
---------------------------------------------------------------------
* possible_formations    formation candidates (high + emerging + early) whose latest activity is
                         within RECENT_DAYS of the data's as-of time
* people_to_meet         this ISO week's stored meet list (``services.meet``)
* companies_accelerating tracked companies with a positive like-for-like 7-day ANTI SCORE delta,
                         or a measured growth-rate acceleration at/above ACCEL_PCT of the cohort
* signal_collisions      tracked companies + people whose collision strength is HIGH or MEDIUM
                         (stored ``strength``; else ``scoring.collision.collision_strength``)
* strange_signals        detected strange-signal patterns

WHAT CHANGED ranking (deterministic)
------------------------------------
Every candidate item gets ``rank = importance × recency``::

    importance  formation: tier (high 1.0 · emerging 0.8 · early 0.55) + score/400
                person:    meet priority (HIGH 0.9 · MEDIUM 0.72 · WATCH 0.5) + confidence bonus
                company:   0.5 + min(Δ7d, 10)/40 (+0.1 measured acceleration) + confidence bonus
                collision: strength (HIGH 0.85 · MEDIUM 0.62) + confidence bonus
                strange:   0.45 + 0.35 × strength
                research:  0.4
    recency     max(0.25, 1 / (1 + age_days / 7))  age of the newest backing evidence

Selection interleaves kinds: a first pass takes at most PER_KIND items of each kind (strongest
first), a second pass fills the remaining slots; one item per entity (a formation also covers its
anchor org); items whose newest evidence is older than RECENT_DAYS are not "changes" and are dropped. Headlines and why-now lines
are deterministic strings built from stored rows — never model text.

``new=today|week`` keeps only entities first detected within 1 / 7 days of the data's as-of time.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from radar.models import (
    Company,
    FormationCandidate,
    MeetingRecommendation,
    Person,
    ScoreSnapshot,
    WatchlistItem,
)
from radar.schemas.api import EntityRef, EvidenceItem, FormationCard, IntelCard, MeetCard, StrangeSignal
from radar.schemas.feeds_v2 import ChangeItem, HomeFeed, HomeSummary
from radar.services import feed, freshness, meet
from radar.services import formation as F
from radar.services import presenters as P
from radar.services.memo import memoized
from radar.settings import get_settings

RECENT_DAYS = 30
ACCEL_PCT = 0.75  # measured growth-rate change at/above this cohort percentile counts as accelerating
COUNT_POOL = 250  # tracked entities considered for the accelerating / collision counts
WHAT_CHANGED_LIMIT = 8
PER_KIND = 2
FORMATION_LIMIT, MEET_CARDS, COMPANY_LIMIT, STRANGE_LIMIT = 3, 5, 6, 3
NEW_WINDOWS = {"today": 1, "week": 7}

TIER_WEIGHT = {"high": 1.0, "emerging": 0.8, "early": 0.55}
PRIORITY_WEIGHT = {"HIGH": 0.9, "MEDIUM": 0.72, "WATCH": 0.5}
STRENGTH_WEIGHT = {"HIGH": 0.85, "MEDIUM": 0.62}
CONF_BONUS = {"HIGH CONFIDENCE": 0.1, "MEDIUM CONFIDENCE": 0.05, "EARLY SIGNAL": 0.0}
FAMILY_LABEL = {"github": "GitHub", "research": "Research", "hiring": "Hiring", "web": "Web",
                "community": "Community", "social": "Social"}


class InvalidWindow(ValueError):
    pass


# ── small shared helpers (also used by the Daily Radar v2 in services.feed) ──


def confidence_for(n_families: int) -> str:
    """Evidence breadth: 3+ independent families HIGH, 2 MEDIUM, else EARLY SIGNAL (= services.meet)."""
    return meet.confidence_for(n_families)


def evidence_families(evidence: Iterable[EvidenceItem]) -> list[str]:
    """Independent source families (freshness.SOURCE_FAMILY) behind a list of evidence items."""
    out: list[str] = []
    for e in evidence:
        fam = freshness.SOURCE_FAMILY.get(e.source_type or "")
        if fam and fam not in out:
            out.append(fam)
    return out


def family_names(families: Iterable[str]) -> list[str]:
    out: list[str] = []
    for f in families:
        f = {"arxiv": "research", "jobs": "hiring", "website": "web", "hackernews": "community", "x": "social",
             "hiring/web": "hiring"}.get(f, f)
        lab = FAMILY_LABEL.get(f, f.replace("_", " ").title())
        if lab not in out:
            out.append(lab)
    return out


def newest(evidence: Iterable[EvidenceItem]) -> datetime | None:
    ats = [e.event_at for e in evidence if e.event_at is not None]
    return max(ats) if ats else None


def recency(at: datetime | None, anchor: datetime) -> float:
    if at is None:
        return 0.25
    age = max((anchor - at).total_seconds() / 86400, 0.0)
    return max(0.25, 1 / (1 + age / 7))


def short_date(d: datetime | None) -> str:
    return f"{d:%b} {d.day}" if d else "date unknown"


def collision_strength(col: dict[str, Any] | None) -> str | None:
    """HIGH | MEDIUM | LOW from a stored collision dict (``strength`` when present)."""
    if not col:
        return None
    s = col.get("strength")
    if s in ("HIGH", "MEDIUM", "LOW"):
        return s
    fams, score = col.get("families") or [], float(col.get("score") or 0)
    try:
        from radar.scoring.collision import collision_strength as cs

        return cs(len(fams), score)
    except ImportError:  # pragma: no cover — fallback documented in the module docstring
        return "MEDIUM" if score >= 0.5 and len(fams) >= 2 else "LOW"


def acceleration_pct(snap: ScoreSnapshot | None) -> float | None:
    """Measured growth-rate change percentile (None without metric history)."""
    raw = (((snap.components or {}) if snap else {}).get("acceleration") or {}).get("raw") or {}
    acc = raw.get("acc")
    return float(acc) if isinstance(acc, (int, float)) else None


def card_confidence(card: Any, evidence: Sequence[EvidenceItem]) -> str:
    """The card's own confidence label when the presenter provides one, else evidence breadth."""
    c = getattr(card, "confidence", None)
    if c in CONF_BONUS:
        return c
    fams = set(evidence_families(evidence))
    col = getattr(card, "collision", None)
    if col is not None:
        fams |= set(getattr(col, "families", []) or [])
    return confidence_for(len(fams))


def resolve_window(new: str | None) -> int | None:
    if new in (None, "", "all"):
        return None
    if new not in NEW_WINDOWS:
        raise InvalidWindow(f"Unknown window {new!r}. Valid: today, week")
    return NEW_WINDOWS[new]


@dataclass
class _Cand:
    item: Any  # ChangeItem (home) or feeds_v2.DailyItem (daily v2): needs kind, headline, entity
    rank: float
    also: tuple[str, ...] = ()  # other entity ids this item covers (a formation's anchor org)

    def keys(self) -> set[str]:
        ent = self.item.entity
        return {ent.id if ent is not None else self.item.headline, *self.also}


Cand = _Cand


def select_changes(cands: Sequence[_Cand], limit: int = WHAT_CHANGED_LIMIT, per_kind: int = PER_KIND
                   ) -> list[Any]:
    """Interleave kinds (first pass ≤ per_kind each, then fill), one item per entity, best first."""
    ordered = sorted(cands, key=lambda c: (-c.rank, c.item.headline))
    chosen: list[_Cand] = []
    seen: set[str] = set()
    for first in (True, False):
        for c in ordered:
            if len(chosen) >= limit:
                break
            if c.keys() & seen:
                continue
            if first and sum(1 for x in chosen if x.item.kind == c.item.kind) >= per_kind:
                continue
            chosen.append(c)
            seen |= c.keys()
    chosen.sort(key=lambda c: (-c.rank, c.item.headline))
    return [c.item for c in chosen]


# ── change items per kind ────────────────────────────────────────────────────


def formation_change(card: FormationCard, anchor: datetime) -> _Cand:
    n = len(card.members)
    anchor_name = card.anchor.name
    if card.kind == "team" and n >= 2:
        headline = f"{n} people active around {anchor_name}"
    elif card.members:
        headline = f"{card.members[0].name} — formation signals around {anchor_name}" \
            if card.members[0].name != anchor_name else f"{anchor_name} — formation signals"
    else:
        headline = f"{anchor_name} — formation signals"
    bits = []
    if card.why_now:
        e = card.why_now[0]
        bits.append(e.title + (f" ({short_date(e.event_at)})" if e.event_at else ""))
    fams = family_names(card.families)
    if len(fams) >= 2:
        bits.append(f"{len(fams)} independent source families ({', '.join(fams)})")
    elif card.independent_signals:
        bits.append(f"{card.independent_signals} signal{'s' if card.independent_signals != 1 else ''}"
                    + (f" from {fams[0]}" if fams else ""))
    bits.append(f"first detected {short_date(card.first_detected_at)}")
    at = card.latest_activity_at or card.first_detected_at
    conf = confidence_for(len(card.families))
    item = ChangeItem(
        kind="formation", headline=headline, why_now=" · ".join(bits),
        entity=EntityRef(type="formation", id=card.id, slug=card.slug, name=anchor_name),
        evidence=list(card.why_now[:4]), confidence=conf, at=at, label=card.label, is_demo=card.is_demo)
    imp = TIER_WEIGHT.get(card.tier, 0.5) + card.score / 400 + CONF_BONUS[conf]
    return _Cand(item, imp * recency(at, anchor), (card.anchor.id,))


def meet_change(card: MeetCard, anchor: datetime) -> _Cand:
    p = card.person
    headline = f"Meet {p.name}" + (f" — {card.area}" if card.area and card.area != "Unclassified" else "")
    if card.what_changed:
        why = card.what_changed[0]
    elif card.why_now:
        why = card.why_now[0].title
    elif card.why_person:
        why = card.why_person[0]
    else:
        why = f"Ranked #{card.rank} on this week's meet list"
    at = newest(card.why_now)
    item = ChangeItem(
        kind="person", headline=headline, why_now=why,
        entity=EntityRef(type="person", id=p.id, slug=p.slug, name=p.name), evidence=list(card.why_now[:4]),
        confidence=card.confidence, at=at, label=f"MEET · {card.priority}", is_demo=p.is_demo)
    imp = PRIORITY_WEIGHT.get(card.priority, 0.5) + CONF_BONUS.get(card.confidence, 0.0)
    return _Cand(item, imp * recency(at, anchor))


def company_change(card: IntelCard, acc: float | None, at: datetime | None, anchor: datetime) -> _Cand:
    d7 = card.delta_7d
    if d7 is not None and d7 > 0:
        headline = f"{card.name}: ANTI SCORE +{d7:g} in 7 days (now {card.score})"
    elif acc is not None:
        headline = f"{card.name}: growth rate accelerating ({_ordinal(acc * 100)} percentile of tracked companies)"
    else:
        headline = f"{card.name}: ANTI SCORE {card.score}"
    ev = [e for e in card.why_surfaced if e.source_type != "system" or e.source_url][:4]
    why = getattr(card, "why_now_sentence", None) or (card.why_surfaced[0].title if card.why_surfaced else
                                                      "No dated evidence on record")
    # Families come from the same stored snapshot collision the company row renders (never re-derived).
    col = card.collision
    if col is not None and len(col.families) >= 2:
        labels = col.family_labels or family_names(col.families)
        why += f" · {col.sentence or col.summary} ({', '.join(labels)})"
    conf = card_confidence(card, ev)
    at = at or newest(ev)
    item = ChangeItem(
        kind="company", headline=headline, why_now=why,
        entity=EntityRef(type="company", id=card.id, slug=card.slug, name=card.name), evidence=ev,
        confidence=conf, at=at, label="ACCELERATING" if (d7 or 0) > 0 or acc is not None else None,
        is_demo=card.is_demo)
    imp = 0.5 + min(max(d7 or 0, 0), 10) / 40 + (0.1 if acc is not None and acc >= ACCEL_PCT else 0) \
        + CONF_BONUS[conf]
    return _Cand(item, imp * recency(at, anchor))


def _ordinal(v: float) -> str:
    n = round(v)
    suf = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def collision_change(entity: Company | Person, etype: str, col: dict[str, Any], strength: str,
                     anchor: datetime) -> _Cand:
    ev: list[EvidenceItem] = []
    for e in col.get("evidence") or []:
        try:
            ev.append(EvidenceItem.model_validate(e))
        except ValueError:
            continue
    fams = col.get("families") or []
    labels = list(col.get("family_labels") or []) or family_names(fams)
    sentence = col.get("sentence") or col.get("summary") or f"{len(fams)} independent sources"
    name = P.display_name(entity).name
    why = f"{' + '.join(labels)} signals collided" if labels else "Independent signals collided"
    if ev:
        why += f" · newest: {sorted(ev, key=lambda e: e.event_at or anchor, reverse=True)[0].title}"
    at = newest(ev)
    conf = confidence_for(len(fams))
    item = ChangeItem(
        kind="collision", headline=f"{name}: {sentence}", why_now=why,
        entity=EntityRef(type=etype, id=str(entity.id), slug=entity.slug, name=name), evidence=ev[:4],
        confidence=conf, at=at, label=f"{strength} COLLISION", is_demo=bool(entity.is_demo))
    imp = STRENGTH_WEIGHT.get(strength, 0.4) + CONF_BONUS[conf]
    return _Cand(item, imp * recency(at, anchor))


def strange_change(s: StrangeSignal, anchor: datetime) -> _Cand | None:
    if not s.entities:
        return None
    e = s.entities[0]
    at = newest(s.evidence) or s.detected_at
    conf = confidence_for(len(evidence_families(s.evidence)))
    detail = s.detail if len(s.detail) <= 220 else s.detail[:217].rstrip() + "…"
    item = ChangeItem(
        kind="strange", headline=s.headline, why_now=detail,
        entity=EntityRef(type=str(e.get("type") or "company"), id=str(e.get("id")), slug=e.get("slug"),
                         name=str(e.get("name") or s.headline)),
        evidence=list(s.evidence[:4]), confidence=conf, at=at, label="STRANGE SIGNAL", is_demo=s.is_demo)
    return _Cand(item, (0.45 + 0.35 * s.strength) * recency(at, anchor))


# ── sections ─────────────────────────────────────────────────────────────────


def _formations(session: Session, *, include_demo: bool, anchor: datetime, cutoff: datetime | None
                ) -> tuple[int, list[FormationCard]]:
    conds = [FormationCandidate.latest_activity_at >= anchor - timedelta(days=RECENT_DAYS),
             FormationCandidate.tier.in_(F.TIERS)]
    if not include_demo:
        conds.append(FormationCandidate.is_demo.is_(False))
    if cutoff is not None:
        conds.append(FormationCandidate.first_detected_at >= cutoff)
    n = session.scalar(select(func.count()).select_from(FormationCandidate).where(*conds)) or 0
    tier_rank = case({"high": 0, "emerging": 1}, value=FormationCandidate.tier, else_=2)
    rows = list(session.scalars(
        select(FormationCandidate).where(*conds)
        .order_by(tier_rank, FormationCandidate.score.desc(), FormationCandidate.latest_activity_at.desc(),
                  FormationCandidate.slug).limit(FORMATION_LIMIT + 2)))
    return n, F.formation_cards(session, rows)


def _people(session: Session, *, include_demo: bool, cutoff: datetime | None) -> list[MeetCard]:
    cards = meet.meet_feed(session, "current", include_demo=include_demo).people
    if cutoff is not None:
        cards = [c for c in cards if c.person.first_seen_at and c.person.first_seen_at >= cutoff]
    return cards


def _new_ok(e: Any, cutoff: datetime | None) -> bool:
    return cutoff is None or bool(getattr(e, "first_seen_at", None) and e.first_seen_at >= cutoff)


def _tracked(session: Session, *, include_demo: bool, cutoff: datetime | None
             ) -> tuple[list[Company], list[Person], dict[uuid.UUID, ScoreSnapshot]]:
    companies = [c for c in feed.candidate_companies(session, include_demo=include_demo, pool=COUNT_POOL)
                 if _new_ok(c, cutoff)]
    people = [p for p in feed.candidate_people(session, include_demo=include_demo, pool=COUNT_POOL)
              if _new_ok(p, cutoff)]
    snaps = P.latest_snapshots(session, [c.id for c in companies] + [p.id for p in people])
    return companies, people, snaps


def build_home(session: Session, *, new: str | None, include_demo: bool) -> HomeFeed:
    days = resolve_window(new)
    as_of = freshness.feed_as_of(session)
    anchor = as_of.anchor
    cutoff = anchor - timedelta(days=days) if days else None

    n_form, formations = _formations(session, include_demo=include_demo, anchor=anchor, cutoff=cutoff)
    people = _people(session, include_demo=include_demo, cutoff=cutoff)
    companies, persons, snaps = _tracked(session, include_demo=include_demo, cutoff=cutoff)

    # Companies accelerating: like-for-like 7-day delta (P.score_deltas never diffs live vs reconstructed)
    # or measured growth-rate acceleration.
    c_snaps = {c.id: snaps[c.id] for c in companies if c.id in snaps}
    d7 = P.score_deltas(session, c_snaps, 7)
    acc = {cid: a for cid, s in c_snaps.items() if (a := acceleration_pct(s)) is not None and a >= ACCEL_PCT}
    accelerating = [c for c in companies if (d7.get(c.id) or 0) > 0 or c.id in acc]
    companies_basis, companies_note = "accelerating", None
    pool = accelerating
    if not accelerating:
        companies_basis = "top_ranked"
        companies_note = ("No tracked company shows a like-for-like 7-day ANTI SCORE rise or measured "
                          "acceleration yet — showing the highest-ranked companies instead.")
        pool = companies[:feed.POOL]
    ranked = feed._cap_demo(feed._rank(session, pool), COMPANY_LIMIT)
    company_cards = P.company_cards(session, [r.entity for r in ranked])
    newest_at = {str(r.entity.id): r.newest for r in ranked}

    # Signal collisions (HIGH / MEDIUM) across tracked companies and people.
    collisions: list[tuple[Any, str, dict[str, Any], str]] = []
    for e, et in [*((c, "company") for c in companies), *((p, "person") for p in persons)]:
        s = snaps.get(e.id)
        col = (s.collision if s else None) or {}
        st = collision_strength(col)
        if st in ("HIGH", "MEDIUM"):
            collisions.append((e, et, col, st))
    collisions.sort(key=lambda t: (t[3] != "HIGH", -float(t[2].get("score") or 0), t[0].slug))

    strange = feed.strange_signals(session, include_demo=include_demo, limit=None)
    if cutoff is not None:
        new_ids = {str(i) for model in (Company, Person)
                   for i in session.scalars(select(model.id).where(model.first_seen_at >= cutoff))}
        strange = [s for s in strange if any(str(e.get("id")) in new_ids for e in s.entities)]

    cands: list[_Cand] = [formation_change(c, anchor) for c in formations[:4]]
    cands += [meet_change(c, anchor) for c in people[:3]]
    if companies_basis == "accelerating":
        cands += [company_change(c, acc.get(uuid.UUID(c.id)), newest_at.get(c.id), anchor)
                  for c in company_cards[:3]]
    cands += [collision_change(e, et, col, st, anchor) for e, et, col, st in collisions[:3]]
    cands += [c for s in strange[:2] if (c := strange_change(s, anchor)) is not None]
    for p in feed.recent_papers(session, include_demo=include_demo, days=days or 7, limit=1, with_why=False):
        ev = [EvidenceItem(title=p.title, source_type="arxiv", source_url=p.url, event_at=p.published_at)]
        why = f"Published {short_date(p.published_at)}" + (" with code" if p.code_urls else "")
        if p.linked_entities:
            why += " · linked to " + ", ".join(str(e.get("name")) for e in p.linked_entities[:2])
        cands.append(_Cand(ChangeItem(
            kind="research", headline=p.title, why_now=why,
            entity=EntityRef(type="paper", id=p.id, slug=p.arxiv_id, name=p.title), evidence=ev,
            confidence="MEDIUM CONFIDENCE" if p.code_urls else "EARLY SIGNAL", at=p.published_at,
            label="RESEARCH", is_demo=p.is_demo), 0.4 * recency(p.published_at, anchor)))

    floor = anchor - timedelta(days=RECENT_DAYS)
    cands = [c for c in cands if c.item.at is None or c.item.at >= floor]
    fresh = freshness.provider_freshness(session)
    summary = HomeSummary(
        updated_at=max(fresh.values()) if fresh else None, data_as_of=as_of.data_as_of,
        is_snapshot=as_of.is_snapshot, snapshot_age_days=as_of.snapshot_age_days,
        possible_formations=n_form, people_to_meet=len(people), companies_accelerating=len(accelerating),
        signal_collisions=len(collisions), strange_signals=len(strange),
    )
    return HomeFeed(
        summary=summary, what_changed=select_changes(cands), formations=formations[:FORMATION_LIMIT],
        people_to_meet=people[:MEET_CARDS], companies=company_cards, companies_basis=companies_basis,
        companies_note=companies_note, strange_signals=strange[:STRANGE_LIMIT], new=new or None,
        demo_mode=include_demo, snapshot_note=feed.snapshot_note(as_of, include_demo=include_demo),
    )


def home_feed(session: Session, new: str | None = None, *, include_demo: bool | None = None) -> HomeFeed:
    """GET /api/home. Memoized on the data version plus formation / meet / watchlist state."""
    include_demo = get_settings().demo_synthetic if include_demo is None else include_demo
    resolve_window(new)  # validate before touching the memo
    version = session.execute(select(
        select(func.max(FormationCandidate.updated_at)).scalar_subquery(),
        select(func.max(MeetingRecommendation.created_at)).scalar_subquery(),
        select(func.count()).select_from(WatchlistItem).scalar_subquery(),
    )).one()
    key = ("home", new or None, include_demo, meet.iso_week(), *tuple(version))
    return memoized(session, key, lambda: build_home(session, new=new, include_demo=include_demo))

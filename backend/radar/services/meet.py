"""WHO SHOULD WE MEET THIS WEEK — a deterministic weekly ranking of ~5 people.

Rows are stored in ``meeting_recommendations`` keyed by ISO week (``YYYY-Www``, computed in
America/New_York) so the week view is stable: the first read of a week computes and stores it
(idempotent; concurrent readers converge on the unique (week, person_id) key), later reads replay
the stored rows, and only an explicit refresh (admin ``POST /api/meet/refresh``) recomputes it.
Past weeks stay readable as history.

Candidates
----------
Non-dismissed people that are not automation / AI coding-agent accounts
(``radar.providers.github.is_bot``), not brand accounts named after an organization (a user
account called like the org it belongs to), with a *current* Founder Score (latest snapshot no
older than ``SCORE_MAX_AGE_DAYS``) and at least one piece of dated evidence. Demo people only
when synthetic demo data is enabled.

Meet score (0..100, every input already stored by the scoring pipeline)
------------------------------------------------------------------------
* founder    30 × Founder Score / 100
* formation  25 × best Formation Score / 100 of a candidate the person anchors or is a member of
* thesis     10 × Founder-Score thesis-fit component (value / max)
* recency    10 × (1 − age / 30 d) of the newest *own* dated signal (measurement signals, whose
             date is the observation time, never count as a change)
* attention  10 × Founder-Score attention-gap component (value / max)
* collision  15 × min(families, 3) / 3 — independent source families (github, research, community,
             web, hiring, social) among the person's own evidence in the last 90 days
* feedback   ± per-person analyst labels on the person or their formation anchor:
             strong_founder +6, interesting_shift +4, useful +3; weak_team −6, too_obvious −6,
             not_interesting −4, already_known −4, wrong_thesis −3; total capped at ±10.
             (Global learned preference weights already act inside the Founder Score — not
             counted twice.)
* obviousness −25 when the person has > 20k followers, or > 1k followers and a current affiliation
             with an established organization (``scoring.formation._established_employer``) —
             waived when the best Formation Score is ≥ 60 (exceptional formation evidence).

"Own evidence" = signals recorded on the person, signals linked to the person
(``signal_entities``) and the signals the Founder Score cites as evidence for its components.

Ranking: meet score desc, then Founder Score desc, then person id (total order → reproducible).
Diversity: at most 2 people from the same formation candidate (or, without one, the same
normalized current company) per week.

Confidence = evidence breadth: ≥ 3 independent families → HIGH CONFIDENCE, 2 → MEDIUM
CONFIDENCE, otherwise EARLY SIGNAL. Priority: HIGH needs meet score ≥ 60 and ≥ 2 families;
MEDIUM needs ≥ 40; otherwise WATCH.

Language: a person is only called a founder when a founder relation (company_people.is_founder)
backs it; formation membership is phrased with the candidate's stored label ("EMERGING TEAM",
"POSSIBLE FORMATION", "FORMATION SIGNALS DETECTED"). Profile affiliations are marked
self-reported.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    CompanyPerson,
    FormationCandidate,
    MeetingRecommendation,
    Person,
    ResearchReport,
    ScoreSnapshot,
    Signal,
    SignalEntity,
    UserFeedback,
)
from radar.pipeline.aliases import org_key
from radar.providers.github import is_bot
from radar.schemas.api import EvidenceItem, MeetCard, MeetFeed, MeetFormation
from radar.scoring.features import MEASUREMENT_SIGNAL_TYPES
from radar.scoring.formation import FORMATION_FAMILY
from radar.services import presenters as P
from radar.services.formation import include_demo_default, subtopic_label

log = logging.getLogger(__name__)

WEEK_TZ = ZoneInfo("America/New_York")
WEEK_RE = re.compile(r"^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$")
MEET_LIMIT = 5
MAX_PER_GROUP = 2
SCORE_MAX_AGE_DAYS = 14
RECENCY_DAYS = 30
EVIDENCE_DAYS = 90
OBVIOUS_FOLLOWERS = 20_000
ESTABLISHED_PERSON_FOLLOWERS = 1_000
EXCEPTIONAL_FORMATION = 60.0
OBVIOUS_PENALTY = 25.0
FEEDBACK_CAP = 10.0
HISTORY_WEEKS = 26

WEIGHTS: dict[str, float] = {"founder": 30, "formation": 25, "thesis": 10, "recency": 10, "attention": 10,
                             "collision": 15}
COLLISION_FULL = 3
FEEDBACK_POINTS: dict[str, float] = {
    "strong_founder": 6, "interesting_shift": 4, "useful": 3,
    "weak_team": -6, "too_obvious": -6, "not_interesting": -4, "already_known": -4, "wrong_thesis": -3,
}
PRIORITY_HIGH, PRIORITY_MEDIUM = 60.0, 40.0
WHY_NOW_LIMIT = 5
WHAT_CHANGED_LIMIT = 4
# Signal types that describe a change worth mentioning under "what changed".
CHANGE_TYPES = frozenset({"affiliation_changed", "bio_changed", "org_created", "repo_created", "formation_language",
                          "x_formation_post", "paper_published", "paper_with_code", "release_published", "hn_launch",
                          "hn_show", "young_org_team", "author_recurrence"})
_FOUNDER_COMPONENTS = ("technical_output", "research", "pedigree", "formation")


class InvalidWeek(ValueError):
    pass


# ── weeks ────────────────────────────────────────────────────────────────────


def iso_week(dt: datetime | None = None) -> str:
    """ISO week ('YYYY-Www') of ``dt`` (default: the data clock's now) in America/New_York."""
    local = (dt or clock.now()).astimezone(WEEK_TZ)
    y, w, _ = local.isocalendar()
    return f"{y}-W{w:02d}"


def resolve_week(week: str | None) -> str:
    if week is None or week == "current":
        return iso_week()
    if not WEEK_RE.match(week):
        raise InvalidWeek("week must be 'current' or ISO 'YYYY-Www'")
    return week


# ── evidence & features ──────────────────────────────────────────────────────


@dataclass
class Evidence:
    signals: list[Signal] = field(default_factory=list)  # deduped, newest first

    def families(self, since: datetime) -> list[str]:
        return sorted({f for s in self.signals if s.event_at >= since
                       and (f := FORMATION_FAMILY.get(s.source_type or ""))})

    def newest_change(self, now: datetime) -> Signal | None:
        for s in self.signals:  # newest first
            if s.signal_type not in MEASUREMENT_SIGNAL_TYPES and s.event_at <= now + timedelta(days=1):
                return s
        return None


@dataclass
class Ranked:
    person: Person
    snap: ScoreSnapshot
    score: float
    features: dict[str, float]
    families: list[str]
    evidence: Evidence
    candidate: FormationCandidate | None
    obvious: str | None
    feedback: dict[str, float]
    group: str | None

    @property
    def founder_score(self) -> float:
        return float(self.snap.score)


def _comp_ratio(snap: ScoreSnapshot, key: str) -> float:
    c = (snap.components or {}).get(key) or {}
    mx = float(c.get("max") or 0)
    return max(0.0, min(1.0, float(c.get("value") or 0) / mx)) if mx else 0.0


def _evidence_signal_ids(snap: ScoreSnapshot) -> set[uuid.UUID]:
    out: set[uuid.UUID] = set()
    for c in (snap.components or {}).values():
        if not isinstance(c, dict):
            continue
        for e in P.component_evidence(c):
            try:
                out.add(uuid.UUID(str(e.get("signal_id"))))
            except (TypeError, ValueError):
                continue
    return out


def load_evidence(session: Session, snaps: dict[uuid.UUID, ScoreSnapshot], since: datetime
                  ) -> dict[uuid.UUID, Evidence]:
    """Own evidence per person (batched): direct + linked signals since ``since`` and Founder-Score evidence."""
    ids = list(snaps)
    if not ids:
        return {}
    by_person: dict[uuid.UUID, dict[uuid.UUID, Signal]] = defaultdict(dict)
    for s in session.scalars(select(Signal).where(Signal.entity_id.in_(ids), Signal.event_at >= since)):
        by_person[s.entity_id][s.id] = s
    linked = session.execute(
        select(SignalEntity.entity_id, Signal).join(Signal, Signal.id == SignalEntity.signal_id)
        .where(SignalEntity.entity_id.in_(ids), Signal.event_at >= since)
    ).all()
    for pid, s in linked:
        by_person[pid][s.id] = s
    cited: dict[uuid.UUID, set[uuid.UUID]] = {pid: _evidence_signal_ids(sn) for pid, sn in snaps.items()}
    all_cited = set().union(*cited.values()) if cited else set()
    rows = {s.id: s for s in session.scalars(select(Signal).where(Signal.id.in_(all_cited)))} if all_cited else {}
    for pid, sids in cited.items():
        for sid in sids:
            if sid in rows and rows[sid].event_at >= since:
                by_person[pid][sid] = rows[sid]
    return {pid: Evidence(sorted(sigs.values(), key=lambda s: (s.event_at, str(s.id)), reverse=True))
            for pid, sigs in by_person.items()}


def _current_snapshots(session: Session, include_demo: bool) -> dict[uuid.UUID, ScoreSnapshot]:
    today = clock.today()
    q = (select(ScoreSnapshot)
         .where(ScoreSnapshot.entity_type == "person", ScoreSnapshot.day <= today,
                ScoreSnapshot.day >= today - timedelta(days=SCORE_MAX_AGE_DAYS))
         .order_by(ScoreSnapshot.entity_id, ScoreSnapshot.day.desc()).distinct(ScoreSnapshot.entity_id))
    if not include_demo:
        q = q.where(ScoreSnapshot.is_demo.is_(False))
    return {s.entity_id: s for s in session.scalars(q)}


def _candidates_by_person(session: Session, include_demo: bool
                          ) -> tuple[dict[uuid.UUID, FormationCandidate], set[uuid.UUID]]:
    """Best formation candidate per person (anchor or member) and the anchor company ids."""
    q = select(FormationCandidate)
    if not include_demo:
        q = q.where(FormationCandidate.is_demo.is_(False))
    best: dict[uuid.UUID, FormationCandidate] = {}
    anchors: set[uuid.UUID] = set()
    for c in session.scalars(q):
        if c.anchor_type == "company":
            anchors.add(c.anchor_id)
        members = set(c.member_ids or [])
        if c.anchor_type == "person":
            members.add(c.anchor_id)
        for pid in members:
            cur = best.get(pid)
            if cur is None or (c.score, str(c.id)) > (cur.score, str(cur.id)):
                best[pid] = c
    return best, anchors


def _brand_keys(session: Session) -> dict[str, str | None]:
    """org key -> the org's own GitHub login, for every organization on the radar."""
    out: dict[str, str | None] = {}
    for name, gh in session.execute(select(Company.name, Company.github_org)):
        for k in (org_key(name), org_key(gh or "")):
            if k:
                out.setdefault(k, (gh or "").lower() or None)
    return out


def is_brand_account(p: Person, brand_keys: dict[str, str | None]) -> bool:
    """A user account named after an organization it is not itself (e.g. a product's account)."""
    handle = (p.github_handle or "").lower()
    for k in (org_key(p.name), org_key(p.github_handle or "")):
        if k and k in brand_keys and brand_keys[k] != handle:
            return True
    return False


def _feedback_points(session: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict[str, float]]:
    ids = list(ids)
    out: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    if not ids:
        return out
    for eid, label in session.execute(select(UserFeedback.entity_id, UserFeedback.label)
                                      .where(UserFeedback.entity_id.in_(ids))):
        pts = FEEDBACK_POINTS.get(label)
        if pts is not None:
            out[eid][label] = pts  # each label counts once per entity
    return out


def _group_key(p: Person, cand: FormationCandidate | None) -> str | None:
    if cand is not None:
        return f"formation:{cand.id}"
    norm = ((p.attrs or {}).get("affiliations_normalized") or {}).get("current_company") or p.current_company
    k = org_key(norm)
    return f"company:{k}" if k else None


def _obviousness(session: Session, p: Person, formation_score: float, young_ids: set[uuid.UUID]) -> str | None:
    if formation_score >= EXCEPTIONAL_FORMATION:
        return None
    followers = p.followers or 0
    if followers > OBVIOUS_FOLLOWERS:
        return f"{followers:,} GitHub followers — already widely followed"
    if followers > ESTABLISHED_PERSON_FOLLOWERS:
        from radar.scoring.formation import _established_employer

        emp = _established_employer(session, p, young_ids)
        if emp is not None:
            return f"{followers:,} followers and currently affiliated with an established organization ({emp.name})"
    return None


def priority_for(score: float, n_families: int) -> str:
    if score >= PRIORITY_HIGH and n_families >= 2:
        return "HIGH"
    if score >= PRIORITY_MEDIUM:
        return "MEDIUM"
    return "WATCH"


def confidence_for(n_families: int) -> str:
    if n_families >= 3:
        return "HIGH CONFIDENCE"
    if n_families == 2:
        return "MEDIUM CONFIDENCE"
    return "EARLY SIGNAL"


def meet_score(features: dict[str, float]) -> float:
    return round(max(0.0, sum(features.values())), 2)


def rank_people(session: Session, *, include_demo: bool | None = None, limit: int = MEET_LIMIT) -> list[Ranked]:
    """The week's ranked people (not persisted). See module docstring for the formula."""
    include_demo = include_demo_default(include_demo)
    now = clock.now()
    snaps = _current_snapshots(session, include_demo)
    if not snaps:
        return []
    people = {p.id: p for p in session.scalars(
        select(Person).where(Person.id.in_(list(snaps)), Person.dismissed.is_(False)))}
    brand = _brand_keys(session)
    people = {pid: p for pid, p in people.items()
              if not (p.github_handle and is_bot(p.github_handle)) and not is_brand_account(p, brand)}
    snaps = {pid: s for pid, s in snaps.items() if pid in people}
    evidence = load_evidence(session, snaps, now - timedelta(days=EVIDENCE_DAYS))
    cands, young_ids = _candidates_by_person(session, include_demo)
    fb = _feedback_points(session, list(people) + [c.anchor_id for c in cands.values()])

    out: list[Ranked] = []
    for pid, p in people.items():
        ev = evidence.get(pid)
        if ev is None or not ev.signals:
            continue  # nothing dated to point at: never recommend on a score alone
        snap = snaps[pid]
        cand = cands.get(pid)
        fscore = float(cand.score) if cand is not None else 0.0
        families = ev.families(now - timedelta(days=EVIDENCE_DAYS))
        newest = ev.newest_change(now)
        recency = 0.0
        if newest is not None:
            age = max(0.0, (now - newest.event_at).total_seconds() / 86400)
            recency = max(0.0, 1 - age / RECENCY_DAYS)
        labels = dict(fb.get(pid, {}))
        if cand is not None and cand.anchor_type == "company":
            for k, v in fb.get(cand.anchor_id, {}).items():
                labels.setdefault(k, v)
        feedback = max(-FEEDBACK_CAP, min(FEEDBACK_CAP, sum(labels.values())))
        obvious = _obviousness(session, p, fscore, young_ids)
        features = {
            "founder": WEIGHTS["founder"] * float(snap.score) / 100,
            "formation": WEIGHTS["formation"] * fscore / 100,
            "thesis": WEIGHTS["thesis"] * _comp_ratio(snap, "thesis_fit"),
            "recency": WEIGHTS["recency"] * recency,
            "attention": WEIGHTS["attention"] * _comp_ratio(snap, "attention_gap"),
            "collision": WEIGHTS["collision"] * min(len(families), COLLISION_FULL) / COLLISION_FULL,
            "feedback": feedback,
            "obviousness": -OBVIOUS_PENALTY if obvious else 0.0,
        }
        features = {k: round(v, 2) for k, v in features.items()}
        out.append(Ranked(person=p, snap=snap, score=meet_score(features), features=features, families=families,
                          evidence=ev, candidate=cand, obvious=obvious, feedback=labels, group=_group_key(p, cand)))
    return select_diverse(out, limit)


def select_diverse(ranked: Sequence[Any], limit: int = MEET_LIMIT, max_per_group: int = MAX_PER_GROUP) -> list[Any]:
    """Total order (score desc, founder score desc, person id), then at most ``max_per_group`` per group."""
    ordered = sorted(ranked, key=lambda r: (-r.score, -r.founder_score, str(r.person.id)))
    picked: list[Any] = []
    per_group: dict[str, int] = defaultdict(int)
    for r in ordered:
        if r.group is not None:
            if per_group[r.group] >= max_per_group:
                continue
            per_group[r.group] += 1
        picked.append(r)
        if len(picked) >= limit:
            break
    return picked


# ── explanations (deterministic) ─────────────────────────────────────────────


def _self_reported(p: Person, field_name: str) -> bool:
    return field_name in ((p.attrs or {}).get("self_reported_fields") or [])


def affiliation_line(p: Person, founder_of: Sequence[str] = ()) -> str:
    parts: list[str] = []
    for name in founder_of:
        parts.append(f"Founder, {name}")
    cur = P.clean_label(p.current_company)
    if cur and cur.strip(" |").lower() not in ("none", "n/a", "-"):
        parts.append(cur + (" (self-reported)" if _self_reported(p, "company") else ""))
    prior = [a for a in map(P.clean_label, p.prior_affiliations or []) if a]
    if prior:
        parts.append("prev. " + ", ".join(prior[-2:]))
    return " · ".join(parts) or "Affiliation unknown"


def area_for(p: Person, snap: ScoreSnapshot, cand: FormationCandidate | None) -> str:
    raw = ((snap.components or {}).get("thesis_fit") or {}).get("raw") or {}
    theme, topic = raw.get("theme"), raw.get("topic")
    if not theme and cand is not None:
        theme, topic = cand.theme, cand.subtopic
    if theme:
        label = P.theme_label(theme)
        sub = subtopic_label(theme, topic) if topic else None
        return f"{label} · {sub}" if sub and sub != label else label
    return ", ".join((p.domains or [])[:2]) or "Unclassified"


def _date(d: datetime) -> str:
    return f"{d:%b} {d.day}"


def _anchor_affiliation(p: Person, anchor: Company | None) -> str | None:
    """A note when the person's own (self-reported) profile points at the candidate's organization."""
    if anchor is None:
        return None
    keys = {k for k in (org_key(anchor.name), org_key(anchor.github_org or "")) if k}
    norm = ((p.attrs or {}).get("affiliations_normalized") or {}).get("current_company") or p.current_company
    site = (p.personal_site or "").lower()
    by_company = any(org_key(norm or "").startswith(k) for k in keys)
    by_site = any(k.replace(" ", "") in site for k in keys if len(k.replace(" ", "")) >= 4)
    if by_company or by_site:
        what = " and ".join(x for x, ok in (("company", by_company), ("website", by_site)) if ok)
        return f"Own GitHub profile names {P.clean_label(anchor.name) or anchor.name} ({what}; self-reported)"
    return None


def why_person(r: Ranked, founder_of: Sequence[str], anchor: Company | None = None) -> list[str]:
    comps = r.snap.components or {}
    out = [f"Founder Score {round(r.founder_score)}/100"]
    for key in _FOUNDER_COMPONENTS:
        c = comps.get(key) or {}
        if (c.get("value") or 0) > 0 and c.get("explanation"):
            text = c["explanation"]
            if key == "pedigree" and r.person.attrs and (r.person.attrs or {}).get("self_reported"):
                text += " (affiliation self-reported)"
            out.append(text)
    for name in founder_of:
        out.append(f"Listed as a founder of {name}")
    if r.candidate is not None:
        out.append(f"In formation candidate \u201c{r.candidate.title}\u201d ({r.candidate.label}) — Formation Score "
                   f"{round(r.candidate.score)}, {r.candidate.tier} tier")
        anchor_note = _anchor_affiliation(r.person, anchor)
        if anchor_note:
            out.append(anchor_note)
    att = r.snap.attention or {}
    if att.get("flagged"):
        out.append(f"Attention gap: technical output at the {att.get('technical'):.0f}th percentile vs public "
                   f"attention at the {att.get('attention'):.0f}th")
    if r.obvious:
        out.append(f"Obviousness penalty: {r.obvious}")
    return out[:6]


def why_now(r: Ranked, now: datetime) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []
    seen: set[str] = set()
    recent = now - timedelta(days=RECENCY_DAYS)
    for s in r.evidence.signals:
        if s.event_at < recent or s.title in seen:
            continue
        seen.add(s.title)
        items.append(P.evidence_from_signal(s))
        if len(items) >= WHY_NOW_LIMIT:
            return items
    if r.candidate is not None:
        for e in r.candidate.why_now or []:
            if isinstance(e, dict) and e.get("title") and e["title"] not in seen:
                seen.add(e["title"])
                try:
                    items.append(EvidenceItem.model_validate(e))
                except ValueError:
                    continue
            if len(items) >= WHY_NOW_LIMIT:
                break
    return items


def what_changed(r: Ranked, now: datetime) -> list[str]:
    out: list[str] = []
    recent = now - timedelta(days=RECENCY_DAYS)
    seen: set[str] = set()
    for s in sorted(r.evidence.signals, key=lambda s: s.event_at):
        if s.event_at < recent or s.event_at > now + timedelta(days=1) or s.signal_type not in CHANGE_TYPES:
            continue
        if s.title in seen:
            continue
        seen.add(s.title)
        out.append(f"{_date(s.event_at)}: {s.title}")
    if r.candidate is not None and r.candidate.first_detected_at and r.candidate.first_detected_at >= recent:
        out.append(f"{_date(r.candidate.first_detected_at)}: formation candidate \u201c{r.candidate.title}\u201d "
                   "first detected")
    return out[-WHAT_CHANGED_LIMIT:]


def thesis_alignment(r: Ranked) -> list[str]:
    out: list[str] = []
    tf = (r.snap.components or {}).get("thesis_fit") or {}
    if (tf.get("value") or 0) > 0 and tf.get("explanation"):
        out.append(tf["explanation"])
    if r.candidate is not None and r.candidate.theme:
        sub = subtopic_label(r.candidate.theme, r.candidate.subtopic)
        out.append(f"Formation candidate theme: {P.theme_label(r.candidate.theme)}" + (f" · {sub}" if sub else ""))
    return out


def _founder_of(session: Session, ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = defaultdict(list)
    if not ids:
        return out
    for pid, name in session.execute(
        select(CompanyPerson.person_id, Company.name).join(Company, Company.id == CompanyPerson.company_id)
        .where(CompanyPerson.person_id.in_(list(ids)), CompanyPerson.is_founder.is_(True))
    ):
        out[pid].append(P.clean_label(name) or name)
    return out


# ── persistence ──────────────────────────────────────────────────────────────


def _rows_for(session: Session, week: str, include_demo: bool) -> list[MeetingRecommendation]:
    q = select(MeetingRecommendation).where(MeetingRecommendation.week == week)
    if not include_demo:
        q = q.where(MeetingRecommendation.is_demo.is_(False))
    return list(session.scalars(q.order_by(MeetingRecommendation.rank)))


def compute_week(session: Session, week: str | None = None, *, force: bool = False,
                 include_demo: bool | None = None) -> list[MeetingRecommendation]:
    """Compute and store ``week`` (default: current) unless it exists; ``force`` replaces it."""
    week = resolve_week(week)
    include_demo = include_demo_default(include_demo)
    existing = list(session.scalars(select(MeetingRecommendation).where(MeetingRecommendation.week == week)))
    if existing and not force:
        return _rows_for(session, week, include_demo)
    now = clock.now()
    ranked = rank_people(session, include_demo=include_demo)
    founder_of = _founder_of(session, [r.person.id for r in ranked])
    anchor_ids = {r.candidate.anchor_id for r in ranked if r.candidate is not None and r.candidate.anchor_type == "company"}
    anchors = {c.id: c for c in session.scalars(select(Company).where(Company.id.in_(anchor_ids)))} if anchor_ids else {}
    try:
        with session.begin_nested():
            if existing:
                session.execute(delete(MeetingRecommendation).where(MeetingRecommendation.week == week))
            for i, r in enumerate(ranked, 1):
                fo = founder_of.get(r.person.id, [])
                reasons = {
                    "why_person": why_person(r, fo, anchors.get(r.candidate.anchor_id) if r.candidate else None),
                    "why_now": [e.model_dump(mode="json") for e in why_now(r, now)],
                    "what_changed": what_changed(r, now),
                    "thesis": thesis_alignment(r),
                    "area": area_for(r.person, r.snap, r.candidate),
                    "affiliation_line": affiliation_line(r.person, fo),
                    "families": r.families,
                    "features": r.features,
                    "feedback": r.feedback,
                    "founder_score": round(r.founder_score, 1),
                    "score_day": r.snap.day.isoformat(),
                }
                session.add(MeetingRecommendation(
                    week=week, person_id=r.person.id, rank=i, priority=priority_for(r.score, len(r.families)),
                    score=r.score, reasons=reasons, evidence_count=len(r.evidence.signals),
                    confidence=confidence_for(len(r.families)),
                    formation_candidate_id=r.candidate.id if r.candidate is not None else None,
                    is_demo=bool(r.person.is_demo),
                ))
            session.flush()
    except IntegrityError:  # a concurrent reader stored the week first: theirs wins
        log.info("meet week %s computed concurrently; using stored rows", week)
    return _rows_for(session, week, include_demo)


def refresh_current_week(session: Session) -> dict[str, Any]:
    rows = compute_week(session, force=True)
    return {"week": iso_week(), "people": len(rows)}


def ensure_current_week(session: Session) -> dict[str, Any]:
    """Sweep step: store the current week if missing (never reshuffles a stored week)."""
    rows = compute_week(session)
    return {"week": iso_week(), "people": len(rows)}


# ── API shape ────────────────────────────────────────────────────────────────


def history_weeks(session: Session, limit: int = HISTORY_WEEKS) -> list[str]:
    return list(session.scalars(
        select(MeetingRecommendation.week).group_by(MeetingRecommendation.week)
        .order_by(MeetingRecommendation.week.desc()).limit(limit)))


def latest_brief_ids(session: Session, person_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not person_ids:
        return {}
    rows = session.execute(
        select(ResearchReport.entity_id, ResearchReport.id)
        .where(ResearchReport.kind == "meeting_brief", ResearchReport.entity_id.in_(list(person_ids)),
               ResearchReport.status == "complete")
        .order_by(ResearchReport.entity_id, ResearchReport.created_at.desc()).distinct(ResearchReport.entity_id))
    return {eid: str(rid) for eid, rid in rows}


def meet_cards(session: Session, rows: Sequence[MeetingRecommendation]) -> list[MeetCard]:
    pids = [r.person_id for r in rows]
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(pids)))} if pids else {}
    rows = [r for r in rows if r.person_id in people and not people[r.person_id].dismissed]
    cards = {c.id: c for c in P.person_cards(session, [people[r.person_id] for r in rows])}
    cids = [r.formation_candidate_id for r in rows if r.formation_candidate_id]
    cands = {c.id: c for c in session.scalars(select(FormationCandidate).where(FormationCandidate.id.in_(cids)))
             } if cids else {}
    briefs = latest_brief_ids(session, [r.person_id for r in rows])
    out: list[MeetCard] = []
    for n, r in enumerate(rows, 1):
        reasons = r.reasons or {}
        c = cands.get(r.formation_candidate_id) if r.formation_candidate_id else None
        why = []
        for e in reasons.get("why_now") or []:
            try:
                why.append(EvidenceItem.model_validate(e))
            except ValueError:
                continue
        out.append(MeetCard(
            rank=n, priority=r.priority, confidence=r.confidence, person=cards[str(r.person_id)],
            area=reasons.get("area") or "Unclassified",
            affiliation_line=reasons.get("affiliation_line") or "Affiliation unknown",
            why_person=list(reasons.get("why_person") or []), why_now=why,
            what_changed=list(reasons.get("what_changed") or []),
            thesis_alignment=list(reasons.get("thesis") or []), evidence_count=r.evidence_count,
            formation=MeetFormation(id=str(c.id), slug=c.slug, label=c.label, tier=c.tier, score=round(c.score))
            if c is not None else None,
            brief_report_id=briefs.get(r.person_id), score=round(r.score, 1),
            families=list(reasons.get("families") or []),
        ))
    return out


def meet_feed(session: Session, week: str | None = "current", *, include_demo: bool | None = None) -> MeetFeed:
    """GET /api/meet. The current week is computed once if missing; past weeks are read-only."""
    resolved = resolve_week(week)
    include_demo = include_demo_default(include_demo)
    if resolved == iso_week():
        rows = compute_week(session, resolved, include_demo=include_demo)
    else:
        rows = _rows_for(session, resolved, include_demo)
    generated = session.scalar(select(func.min(MeetingRecommendation.created_at))
                               .where(MeetingRecommendation.week == resolved))
    return MeetFeed(week=resolved, generated_at=generated, as_of=clock.today(), people=meet_cards(session, rows),
                    history_weeks=history_weeks(session))


def resolve_person(session: Session, ref: str) -> Person | None:
    try:
        p = session.get(Person, uuid.UUID(str(ref)))
        if p is not None:
            return p
    except ValueError:
        pass
    return session.scalar(select(Person).where(or_(Person.slug == ref, func.lower(Person.github_handle) == ref.lower())).limit(1))

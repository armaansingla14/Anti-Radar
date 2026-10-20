"""Team Convergence: people who already had a history together now share a new anchor.

Input is a member set that *now* shares an anchor (a young org or repository) plus the
person-person relationships among them. Only relationships that **predate the anchor's
creation** count as prior history:

* COAUTHORED_WITH — a paper dated before the anchor was created;
* CONTRIBUTED_WITH — co-contribution to a *different* repository, overlapping before creation;
* WORKED_WITH — both list the same organization (self-reported, INFERRED), other than the
  anchor itself. Affiliations carry no date; a *prior* affiliation by definition predates
  the current one, a shared *current* employer counts at half strength.

Score (0..1, deterministic)::

    0.45 * coverage          mean over members of their strongest prior tie (0 when none)
  + 0.25 * pair strength     mean noisy-OR strength over connected pairs (recency-weighted)
  + 0.15 * kind diversity    distinct prior relationship kinds / 3
  + 0.15 * families          independent signal families on the anchor / 4
  (with no prior tie at all: only half the families term — signals alone are not convergence)

Strength: HIGH >= 0.6, MEDIUM >= 0.35, else LOW.

Lab alumni (talent pool): when ``lab`` ({member: verified lab contributions, ``features.LabContribution``}) is
given and >= 2 members have verified lab-repository commits:

* LAB_CO_CONTRIBUTED — both committed to the *same* lab repository, the later first commit before the
  anchor was created: a strong prior tie (VERIFIED; CONTRIBUTED_WITH base weight, recency of the overlap's
  end, floored at the shared-lab weight; counts as the co-contribution kind for kind diversity). Skipped when a CONTRIBUTED_WITH relationship already
  carries the same repository (never counted twice).
* LAB_SHARED_ORG — commits to repositories of the same lab org (different repositories, or dates unknown):
  collaboration *context* only (weight 0.35, undated, adds no kind). It is never employment.

Dates come from evidence (paper date; for co-contribution the later of the two people's
``RepoContributor.first_commit_at``). Co-contributions whose first-commit dates are unknown
cannot be shown to predate the anchor: they never score, and the explanation says so.

Optionally ``joined`` ({member: first commit on the anchor's repositories}) reports which
members committed within ``JOIN_WINDOW_DAYS`` of the anchor's creation (a team that started
together). That is team-formation evidence (scored by ``scoring.formation``), not prior
history, so it is explained here but does not change the convergence score.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from itertools import combinations
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from radar.models.core import Relationship
from radar.pipeline.aliases import org_key
from radar.scoring.collision import number_word
from radar.scoring.features import LabContribution

COAUTHORED_WITH = "COAUTHORED_WITH"
CONTRIBUTED_WITH = "CONTRIBUTED_WITH"
WORKED_WITH = "WORKED_WITH"
PAIR_KINDS = (COAUTHORED_WITH, CONTRIBUTED_WITH, WORKED_WITH)

BASE_WEIGHT = {COAUTHORED_WITH: 0.9, CONTRIBUTED_WITH: 0.8, WORKED_WITH: 0.5}
CURRENT_EMPLOYER_FACTOR = 0.5
RECENCY_DAYS = 730.0  # a collaboration two years before the anchor counts e^-1 as much
HIGH, MEDIUM = 0.6, 0.35
W_COVERAGE, W_PAIRS, W_KINDS, W_FAMILIES = 0.45, 0.25, 0.15, 0.15
MAX_FAMILIES = 4
JOIN_WINDOW_DAYS = 30
LAB_CO_CONTRIBUTED = "LAB_CO_CONTRIBUTED"
LAB_SHARED_ORG = "LAB_SHARED_ORG"
LAB_SHARED_ORG_WEIGHT = 0.35
# Kind-diversity bucket per tie kind (lab context adds no kind of its own).
KIND_BUCKET = {COAUTHORED_WITH: COAUTHORED_WITH, CONTRIBUTED_WITH: CONTRIBUTED_WITH, WORKED_WITH: WORKED_WITH,
               LAB_CO_CONTRIBUTED: CONTRIBUTED_WITH}


@dataclass(frozen=True)
class Member:
    id: uuid.UUID
    name: str


@dataclass
class RelLite:
    id: uuid.UUID | None
    kind: str
    a: uuid.UUID
    b: uuid.UUID
    epistemic: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PriorTie:
    rel_id: uuid.UUID | None
    kind: str
    a: uuid.UUID
    b: uuid.UUID
    weight: float
    at: datetime | None
    detail: str  # paper title / repo / org
    evidence: list[dict[str, Any]]
    verified: bool = False  # e.g. shared public GitHub org membership vs self-reported employer


@dataclass
class ConvergenceResult:
    score: float
    strength: str
    explanation: str
    prior_relationships: int
    relationship_ids: list[str]
    families: list[str]
    kinds: list[str]
    connected_members: int
    members: int
    ties: list[PriorTie] = field(default_factory=list)
    undated_cocontributions: int = 0  # co-contributions elsewhere with unknown first-commit dates
    founding_members: list[uuid.UUID] = field(default_factory=list)  # first commit within the join window
    join_dates_unknown: int = 0  # members whose first commit on the anchor is unknown (when ``joined`` given)
    lab_members: list[uuid.UUID] = field(default_factory=list)  # members with verified lab-repository commits

    def to_json(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "strength": self.strength,
            "explanation": self.explanation,
            "relationship_ids": self.relationship_ids,
            "prior_relationships": self.prior_relationships,
            "families": self.families,
            "kinds": self.kinds,
            "connected_members": self.connected_members,
            "members": self.members,
            "undated_cocontributions": self.undated_cocontributions,
            "founding_members": [str(m) for m in self.founding_members],
            "join_dates_unknown": self.join_dates_unknown,
            "lab_members": [str(m) for m in self.lab_members],
        }


def _dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, date):
        return datetime.combine(v, time.min, tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


def strength_label(score: float) -> str:
    return "HIGH" if score >= HIGH else ("MEDIUM" if score >= MEDIUM else "LOW")


def recency(at: datetime | None, created: datetime) -> float:
    if at is None:
        return 1.0
    gap = max((created - at).total_seconds() / 86400, 0.0)
    return math.exp(-gap / RECENCY_DAYS)


def prior_ties(
    rels: Iterable[RelLite],
    anchor_created: date | datetime,
    *,
    anchor_repo_ids: set[str] | None = None,
    anchor_org_keys: set[str] | None = None,
) -> list[PriorTie]:
    """The relationships (one tie per qualifying evidence item) that predate the anchor."""
    created = _dt(anchor_created)
    assert created is not None
    anchor_repo_ids = anchor_repo_ids or set()
    anchor_org_keys = {k for k in (anchor_org_keys or set()) if k}
    ties: list[PriorTie] = []
    for r in rels:
        if r.kind not in PAIR_KINDS:
            continue
        if r.kind == WORKED_WITH:
            items = r.evidence or []
            okey = next((e.get("org_key") for e in items if e.get("org_key")), None) or org_key(
                next((e.get("org") for e in items if e.get("org")), "") or "")
            if not okey or okey in anchor_org_keys:
                continue  # the anchor itself is not a *prior* shared employer
            both_prior = all(e.get("prior") for e in items) if items else False
            w = BASE_WEIGHT[WORKED_WITH] * (1.0 if both_prior else CURRENT_EMPLOYER_FACTOR)
            org = next((e.get("org") for e in items if e.get("org")), okey)
            ties.append(PriorTie(r.id, r.kind, r.a, r.b, w, None, org, items, verified=r.epistemic == "VERIFIED"))
            continue
        for ev in r.evidence or []:
            at = _dt(ev.get("event_at"))
            if at is None or at >= created:
                continue  # undated, or not before the anchor existed
            if r.kind == CONTRIBUTED_WITH and ev.get("repo_id") in anchor_repo_ids:
                continue  # co-contribution on the anchor's own repositories is the anchor, not history
            w = BASE_WEIGHT[r.kind] * recency(at, created)
            ties.append(PriorTie(r.id, r.kind, r.a, r.b, w, at, ev.get("title") or "", [ev]))
    return ties


def undated_cocontributions(rels: Iterable[RelLite], members: set[uuid.UUID],
                            anchor_repo_ids: set[str] | None = None) -> int:
    """Co-contributions among ``members`` on *other* repositories that carry no date (first-commit
    dates unknown), so they cannot be shown to predate the anchor."""
    anchor_repo_ids = anchor_repo_ids or set()
    n = 0
    for r in rels:
        if r.kind != CONTRIBUTED_WITH or r.a not in members or r.b not in members or r.a == r.b:
            continue
        n += sum(1 for ev in r.evidence or [] if _dt(ev.get("event_at")) is None
                 and ev.get("repo_id") not in anchor_repo_ids)
    return n


def _pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    return (a, b) if str(a) < str(b) else (b, a)


def lab_ties(lab: Mapping[uuid.UUID, Sequence[LabContribution]], anchor_created: date | datetime,
             existing: Iterable[PriorTie] = ()) -> list[PriorTie]:
    """Prior ties from verified lab contributions (see module docstring). ``existing`` ties (from the
    relationships table) suppress a lab tie on the same pair and repository / org."""
    created = _dt(anchor_created)
    assert created is not None
    have_repo: set[tuple[tuple[uuid.UUID, uuid.UUID], str]] = set()
    have_org: set[tuple[tuple[uuid.UUID, uuid.UUID], str]] = set()
    for t in existing:
        if t.kind in (CONTRIBUTED_WITH, LAB_CO_CONTRIBUTED):
            have_repo.add((_pair(t.a, t.b), (t.detail or "").lower()))
        elif t.kind == WORKED_WITH:
            have_org.add((_pair(t.a, t.b), org_key(t.detail or "")))
    members = sorted((m for m, cs in lab.items() if cs), key=str)
    out: list[PriorTie] = []
    for a, b in combinations(members, 2):
        pair = _pair(a, b)
        la = {c.full_name.lower(): c for c in lab[a]}
        lb = {c.full_name.lower(): c for c in lab[b]}
        dated = False
        for key in sorted(la.keys() & lb.keys()):
            ca, cb = la[key], lb[key]
            if (pair, key) in have_repo:
                dated = True  # already a CONTRIBUTED_WITH tie on this repository
                continue
            if ca.first_commit_at is None or cb.first_commit_at is None:
                continue
            start = max(ca.first_commit_at, cb.first_commit_at)
            if start >= created:
                continue
            dated = True
            ends = [d for d in (ca.last_commit_at, cb.last_commit_at) if d is not None]
            ev = {"title": ca.full_name,
                  "detail": f"Both committed: {ca.commits:,} and {cb.commits:,} commits (verified)",
                  "source_url": f"https://github.com/{ca.full_name}/graphs/contributors", "source_type": "github",
                  "event_at": start.isoformat(), "end_at": max(ends).isoformat() if ends else None,
                  "years": _span_years([ca.first_commit_at, cb.first_commit_at, *ends]), "verified": True}
            # Recency of the overlap's end (both last active), never weaker than mere shared-lab context.
            last_both = min(ends) if len(ends) == 2 else start
            w = max(LAB_SHARED_ORG_WEIGHT, BASE_WEIGHT[CONTRIBUTED_WITH] * recency(min(last_both, created), created))
            out.append(PriorTie(None, LAB_CO_CONTRIBUTED, pair[0], pair[1], w, start, ca.full_name, [ev],
                                verified=True))
        if dated:
            continue
        orgs_a = {c.org.lower(): c for c in lab[a]}
        orgs_b = {c.org.lower(): c for c in lab[b]}
        for org in sorted(orgs_a.keys() & orgs_b.keys()):
            ca, cb = orgs_a[org], orgs_b[org]
            if (pair, org_key(ca.label)) in have_org or (pair, org_key(ca.org)) in have_org:
                continue
            ev = {"title": f"{ca.label} repositories",
                  "detail": f"{ca.full_name} ({ca.commits:,} commits) and {cb.full_name} ({cb.commits:,} commits), "
                            "verified",
                  "source_url": f"https://github.com/{ca.org}", "source_type": "github", "event_at": None,
                  "verified": True}
            out.append(PriorTie(None, LAB_SHARED_ORG, pair[0], pair[1], LAB_SHARED_ORG_WEIGHT, None, ca.label, [ev],
                                verified=True))
            break  # one shared-lab context tie per pair
    return out


def _mark_lab_cocontributions(ties: list[PriorTie], lab: Mapping[uuid.UUID, Sequence[LabContribution]]) -> None:
    """A dated CONTRIBUTED_WITH tie (relationships table) on a lab repository both members have verified history
    on is the same fact as a lab co-contribution: relabel it (same weight, same kind bucket) so it is explained
    as lab history, with the years of both members' commits."""
    for t in ties:
        if t.kind != CONTRIBUTED_WITH:
            continue
        key = (t.detail or "").lower()
        ca = next((c for c in lab.get(t.a, []) if c.full_name.lower() == key), None)
        cb = next((c for c in lab.get(t.b, []) if c.full_name.lower() == key), None)
        if ca is None or cb is None:
            continue
        ends = [d for d in (ca.last_commit_at, cb.last_commit_at) if d is not None]
        t.kind, t.verified = LAB_CO_CONTRIBUTED, True
        t.evidence = [{**e, "end_at": max(ends).isoformat() if ends else None,
                       "years": _span_years([ca.first_commit_at, cb.first_commit_at, *ends]), "verified": True}
                      for e in t.evidence]


def _span_years(ds: Iterable[datetime | None]) -> str | None:
    ys = sorted({d.year for d in ds if d is not None})
    if not ys:
        return None
    return str(ys[0]) if len(ys) == 1 else f"{ys[0]}\u2013{ys[-1]}"


def _count_of(k: int, n: int) -> str:
    """'two of three members' / 'both members' / 'all three members' (the sentence start is capitalized)."""
    if k == n:
        return "both members" if n == 2 else f"all {number_word(n)} members"
    return f"{number_word(k)} of {number_word(n)} members"


def founding_members(joined: Mapping[uuid.UUID, datetime | date | None], anchor_created: date | datetime,
                     window_days: int = JOIN_WINDOW_DAYS) -> tuple[list[uuid.UUID], int]:
    """(members whose first commit on the anchor is within ``window_days`` of its creation, unknown count)."""
    created = _dt(anchor_created)
    assert created is not None
    out, unknown = [], 0
    for m, at in joined.items():
        d = _dt(at)
        if d is None:
            unknown += 1
        elif (d - created).total_seconds() <= window_days * 86400:
            out.append(m)
    return sorted(out, key=str), unknown


def _names(ids: Sequence[uuid.UUID], members: dict[uuid.UUID, Member]) -> str:
    names = [members[i].name for i in ids if i in members]
    if len(names) <= 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _share(k: int, n: int) -> str:
    if k == n:
        return "both" if n == 2 else (f"all {number_word(n)}" if n > 2 else "the member")
    return f"{k} of {n} members"


def explain(ties: list[PriorTie], members: dict[uuid.UUID, Member], n: int, anchor_name: str,
            created: datetime, families: list[str], *, undated: int = 0,
            founders: Sequence[uuid.UUID] = (), join_unknown: int | None = None,
            window_days: int = JOIN_WINDOW_DAYS) -> str:
    parts: list[str] = []
    by_kind: dict[str, list[PriorTie]] = defaultdict(list)
    for t in ties:
        by_kind[t.kind].append(t)
    # Verified lab history leads.
    if by_kind.get(LAB_CO_CONTRIBUTED):
        repos: dict[str, list[PriorTie]] = defaultdict(list)
        for t in by_kind[LAB_CO_CONTRIBUTED]:
            repos[t.detail].append(t)
        repo, ts = max(repos.items(), key=lambda kv: (len({p for t in kv[1] for p in (t.a, t.b)}), kv[0]))
        people = {p for t in ts for p in (t.a, t.b)}
        spans = [str(e["years"]) for t in ts for e in t.evidence if e.get("years")]
        years = (_span_years(datetime(int(y), 1, 1, tzinfo=UTC) for sp in spans for y in sp.split("\u2013"))
                 if spans else _span_years(_dt(x) for t in ts for e in t.evidence
                                           for x in (e.get("event_at"), e.get("end_at"))))
        sentence = (f"{_count_of(len(people), n)} previously contributed to {repo} (verified commits"
                    f"{', ' + years if years else ''}), before {anchor_name} existed")
        if len(repos) > 1:
            sentence += f"; {len(repos) - 1} other shared lab repositor{'ies' if len(repos) > 2 else 'y'}"
        parts.append(sentence)
    if by_kind.get(LAB_SHARED_ORG):
        orgs_l: dict[str, set[uuid.UUID]] = defaultdict(set)
        for t in by_kind[LAB_SHARED_ORG]:
            orgs_l[t.detail] |= {t.a, t.b}
        org, ppl = max(orgs_l.items(), key=lambda kv: (len(kv[1]), kv[0]))
        parts.append(f"{_count_of(len(ppl), n)} have verified commits to {org} repositories (shared lab context; "
                     "not evidence of employment)")
    if by_kind.get(COAUTHORED_WITH):
        ts = by_kind[COAUTHORED_WITH]
        people = {p for t in ts for p in (t.a, t.b)}
        years = sorted({t.at.year for t in ts if t.at})
        papers = len({t.detail for t in ts})
        when = f" in {years[-1]}" if len(years) == 1 else (f" between {years[0]} and {years[-1]}" if years else "")
        what = "a paper" if papers == 1 else f"{papers} papers"
        parts.append(f"{_share(len(people), n)} co-authored {what}{when}, before {anchor_name} existed")
    if by_kind.get(CONTRIBUTED_WITH):
        ts = by_kind[CONTRIBUTED_WITH]
        people = {p for t in ts for p in (t.a, t.b)}
        repos = sorted({t.detail for t in ts if t.detail})
        where = repos[0] if len(repos) == 1 else f"{len(repos)} other repositories"
        parts.append(f"{_share(len(people), n)} contributed to {where} together earlier")
    if by_kind.get(WORKED_WITH):
        orgs: dict[str, set[uuid.UUID]] = defaultdict(set)
        prior_only: dict[str, bool] = {}
        verified_orgs: set[str] = set()
        for t in by_kind[WORKED_WITH]:
            orgs[t.detail] |= {t.a, t.b}
            if t.verified:
                verified_orgs.add(t.detail)
            prior_only[t.detail] = prior_only.get(t.detail, True) and t.weight >= BASE_WEIGHT[WORKED_WITH]
        org, ppl = max(orgs.items(), key=lambda kv: (len(kv[1]), kv[0]))
        rel = "a prior employer" if prior_only[org] else "an affiliation"
        sentence = (f"{_share(len(ppl), n)} are public GitHub members of {org}" if org in verified_orgs
                    else f"{_share(len(ppl), n)} list {org} as {rel} (self-reported)")
        if len(orgs) > 1:
            sentence += f"; {len(orgs) - 1} other shared affiliation{'s' if len(orgs) > 2 else ''}"
        parts.append(sentence)
    if not parts:
        text = (f"No relationship among the {n} members predates {anchor_name} "
                f"(created {created.date().isoformat()}); they are linked only through it")
    else:
        text = "; ".join(parts)
        text = text[0].upper() + text[1:]
    if undated:
        text += (f". {number_word(undated, True)} co-contribution{'s' if undated != 1 else ''} on other repositories "
                 f"{'have' if undated != 1 else 'has'} no first-commit dates, so {'they' if undated != 1 else 'it'} "
                 f"cannot be shown to predate {anchor_name}")
    if join_unknown is not None:
        if len(founders) >= 2:
            text += (f". {_names(list(founders), members)} made their first commits to {anchor_name} within "
                     f"{window_days} days of its creation")
        elif len(founders) == 1:
            text += f". Only {_names(list(founders), members)} committed to {anchor_name} within {window_days} days of its creation"
        if join_unknown:
            text += (f". First-commit dates on {anchor_name} are unknown for {_share(join_unknown, n)}, "
                     "so when they joined cannot be shown")
    if families:
        text += f". Signals on {anchor_name} come from {number_word(len(families))} independent source " \
                f"famil{'ies' if len(families) != 1 else 'y'} ({', '.join(families)})"
    return text + "."


def convergence(
    members: Sequence[Member],
    rels: Iterable[RelLite],
    anchor_created: date | datetime,
    *,
    anchor_name: str,
    families: Sequence[str] = (),
    anchor_repo_ids: set[str] | None = None,
    anchor_org_keys: set[str] | None = None,
    joined: Mapping[uuid.UUID, datetime | date | None] | None = None,
    join_window_days: int = JOIN_WINDOW_DAYS,
    lab: Mapping[uuid.UUID, Sequence[LabContribution]] | None = None,
) -> ConvergenceResult:
    """Pure convergence score for ``members`` now sharing an anchor created at ``anchor_created``.

    ``joined`` (optional): each member's first commit on the anchor's repositories (None = unknown).
    ``lab`` (optional): each member's verified lab contributions (lab-alumni ties, see module docstring)."""
    created = _dt(anchor_created)
    assert created is not None
    mem = {m.id: m for m in members}
    n = len(mem)
    fams = sorted(dict.fromkeys(families))
    rels = list(rels)
    undated = undated_cocontributions(rels, set(mem), anchor_repo_ids)
    founders: list[uuid.UUID] = []
    join_unknown: int | None = None
    if joined is not None:
        founders, join_unknown = founding_members({m: joined.get(m) for m in mem}, created, join_window_days)
    ties = [t for t in prior_ties(rels, created, anchor_repo_ids=anchor_repo_ids, anchor_org_keys=anchor_org_keys)
            if t.a in mem and t.b in mem and t.a != t.b]
    lab_members = sorted((m for m in mem if lab and lab.get(m)), key=str)
    if len(lab_members) >= 2:
        assert lab is not None
        _mark_lab_cocontributions(ties, lab)
        ties += lab_ties({m: lab[m] for m in lab_members}, created, ties)
    pair_w: dict[tuple[uuid.UUID, uuid.UUID], list[float]] = defaultdict(list)
    for t in ties:
        pair_w[(t.a, t.b) if str(t.a) < str(t.b) else (t.b, t.a)].append(t.weight)
    connected = {p for pair in pair_w for p in pair}
    pair_str = {pair: 1 - math.prod(1 - min(w, 0.999) for w in ws) for pair, ws in pair_w.items()}
    avg_pair = sum(pair_str.values()) / len(pair_str) if pair_str else 0.0
    # Strength-weighted coverage: each member counts with their strongest prior tie, so a team linked only
    # by a self-reported shared employer covers less than one that published together.
    member_best: dict[uuid.UUID, float] = defaultdict(float)
    for (x, y), v in pair_str.items():
        member_best[x] = max(member_best[x], v)
        member_best[y] = max(member_best[y], v)
    coverage = sum(member_best.values()) / n if n >= 2 else 0.0
    kinds = sorted({t.kind for t in ties})
    kind_buckets = {KIND_BUCKET[t.kind] for t in ties if t.kind in KIND_BUCKET}
    fam_part = min(len(fams), MAX_FAMILIES) / MAX_FAMILIES
    if ties:
        score = (W_COVERAGE * coverage + W_PAIRS * avg_pair + W_KINDS * min(len(kind_buckets), 3) / 3
                 + W_FAMILIES * fam_part)
    else:
        score = W_FAMILIES * fam_part * 0.5
    score = min(max(score, 0.0), 1.0)
    rel_ids = list(dict.fromkeys(str(t.rel_id) for t in ties if t.rel_id is not None))
    return ConvergenceResult(
        score=score,
        strength=strength_label(score),
        explanation=explain(ties, mem, n, anchor_name, created, fams, undated=undated, founders=founders,
                            join_unknown=join_unknown, window_days=join_window_days),
        prior_relationships=len(pair_w),
        relationship_ids=rel_ids,
        families=fams,
        kinds=kinds,
        connected_members=len(connected),
        members=n,
        ties=ties,
        undated_cocontributions=undated,
        founding_members=founders,
        join_dates_unknown=join_unknown or 0,
        lab_members=lab_members,
    )


# ── DB helper ────────────────────────────────────────────────────────────────


def load_member_relationships(session: Session, member_ids: Sequence[uuid.UUID]) -> list[RelLite]:
    """Person-person relationships with both endpoints in ``member_ids``."""
    ids = list(dict.fromkeys(member_ids))
    if len(ids) < 2:
        return []
    rows = session.scalars(
        select(Relationship).where(
            Relationship.kind.in_(PAIR_KINDS),
            and_(Relationship.src_id.in_(ids), Relationship.dst_id.in_(ids)),
        )
    )
    return [RelLite(r.id, r.kind, r.src_id, r.dst_id, r.epistemic, list(r.evidence or [])) for r in rows]


def load_pair_relationships(session: Session, member_sets: Iterable[Sequence[uuid.UUID]]) -> dict[
        frozenset[uuid.UUID], list[RelLite]]:
    """Batch variant: one query for every member set; returns {frozenset(members): rels}."""
    sets = [frozenset(s) for s in member_sets if len(set(s)) >= 2]
    all_ids = list({i for s in sets for i in s})
    out: dict[frozenset[uuid.UUID], list[RelLite]] = {s: [] for s in sets}
    if not all_ids:
        return out
    rels: list[RelLite] = []
    for i in range(0, len(all_ids), 2000):
        chunk = all_ids[i:i + 2000]
        for r in session.scalars(select(Relationship).where(
                Relationship.kind.in_(PAIR_KINDS),
                or_(Relationship.src_id.in_(chunk)))):
            rels.append(RelLite(r.id, r.kind, r.src_id, r.dst_id, r.epistemic, list(r.evidence or [])))
    seen: set[uuid.UUID | None] = set()
    for r in rels:
        if r.id in seen:
            continue
        seen.add(r.id)
        for s in sets:
            if r.a in s and r.b in s:
                out[s].append(r)
    return out


def convergence_for(
    session: Session,
    member_ids: Sequence[uuid.UUID],
    names: dict[uuid.UUID, str],
    anchor_created: date | datetime,
    *,
    anchor_name: str,
    families: Sequence[str] = (),
    anchor_repo_ids: set[str] | None = None,
    anchor_org_keys: set[str] | None = None,
) -> ConvergenceResult:
    rels = load_member_relationships(session, member_ids)
    return convergence([Member(i, names.get(i, str(i))) for i in member_ids], rels, anchor_created,
                       anchor_name=anchor_name, families=families, anchor_repo_ids=anchor_repo_ids,
                       anchor_org_keys=anchor_org_keys)


__all__ = ["JOIN_WINDOW_DAYS", "LAB_CO_CONTRIBUTED", "LAB_SHARED_ORG", "lab_ties", "ConvergenceResult", "Member", "founding_members", "undated_cocontributions", "PriorTie", "RelLite", "convergence", "convergence_for",
           "load_member_relationships", "load_pair_relationships", "prior_ties", "strength_label"]

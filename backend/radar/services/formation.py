"""Formation radar: API shapes built from ``FormationCandidate`` rows.

Candidates (team or person, anchored on a young org / repo / person) are computed by
``scoring/formation.py``; this module only presents them. Everything shown is deterministic
and evidence-backed:

* ``label`` is stored with the candidate (EMERGING TEAM / POSSIBLE FORMATION / FORMATION
  SIGNALS DETECTED) — never "founded" unless a FOUNDED relation backs it.
* ``previous_affiliations`` is the union of members' *normalized* prior organizations. Those
  come from self-described profile fields, so the card carries status INFERRED and the
  self-reported note.
* Detail pages list the stored ``Relationship`` rows among members and from members to the
  anchor / its repositories, a 180-day timeline of signals on the anchor, members and repos,
  and every distinct source URL behind them.

All loaders are batched: one query per entity type for a whole feed, never per card.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from radar import clock
from radar.models import (
    Company,
    FormationCandidate,
    Job,
    PaperAuthor,
    Person,
    Relationship,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
)
from radar.pipeline.aliases import normalize_org
from radar.schemas.api import (
    AntiScore,
    Convergence,
    DataAge,
    EntityRef,
    EvidenceItem,
    FormationCard,
    FormationCounts,
    FormationDetail,
    FormationFeed,
    FormationMember,
    RelationshipOut,
    ScoreComponent,
)
from radar.scoring.extract import person_lab_contributions
from radar.scoring.features import LabContribution
from radar.services import entities as E
from radar.services import presenters as P
from radar.services.freshness import data_age, feed_as_of, light_data_age
from radar.settings import get_settings
from radar.thesis.loader import get_thesis

TIERS = ("high", "emerging", "early")
TIER_LIMIT = 60  # cards per tier in the feed (counts are always totals)
TIMELINE_DAYS = 180
TIMELINE_LIMIT = 60
RELATIONSHIP_LIMIT = 120
REPO_LIMIT = 12
PAPER_LIMIT = 12
WHY_NOW_LIMIT = 6
MEMBER_AFFILIATIONS = 4
HEADLINE_FACTS = 4  # at most, per card
NEW_REPO_FACTS = 2  # new repositories cited per person card
RECENT_REPO_DAYS = 180  # "in N days" up to this age, "since Mon YYYY" beyond
STAR = "\u2605"

# Display order of FORMATION SCORE components (unknown keys follow, largest max first).
# Mirrors scoring/formation.py FORMATION_MAX (kept local so presentation never imports scoring).
COMPONENT_ORDER = ("founder_quality", "team_convergence", "technical_activity", "formation_evidence",
                   "novelty_attention", "novelty", "attention_gap")
COMPONENT_LABELS = {
    "founder_quality": "Founder quality",
    "team_convergence": "Team convergence",
    "technical_activity": "Technical activity",
    "formation_evidence": "Formation evidence",
    "novelty": "Novelty / attention gap",
    "novelty_attention": "Novelty / attention gap",
    "attention_gap": "Attention gap",
}
# Order of relationship kinds in a formation report: the structural ties first.
KIND_ORDER = ("FOUNDED", "MEMBER_OF", "CONTRIBUTED_TO", "CONTRIBUTED_WITH", "COAUTHORED_WITH", "WORKED_WITH",
              "AUTHORED")


class FormationNotFound(LookupError):
    pass


# ── small helpers ────────────────────────────────────────────────────────────


def include_demo_default(include_demo: bool | None) -> bool:
    return get_settings().demo_synthetic if include_demo is None else include_demo


def _uuid(v: Any) -> uuid.UUID | None:
    if isinstance(v, uuid.UUID):
        return v
    try:
        return uuid.UUID(str(v))
    except (TypeError, ValueError):
        return None


def subtopic_label(theme: str | None, subtopic: str | None) -> str | None:
    if not subtopic:
        return None
    th, _, sub = subtopic.partition(".")
    if not sub:  # bare subcategory key: belongs to the candidate's theme
        th, sub = theme or "", subtopic
    t = get_thesis().themes.get(th)
    s = t.subcategories.get(sub) if t else None
    return s.label if s else sub.replace("_", " ").title()


def components_list(raw: dict[str, Any] | None) -> list[ScoreComponent]:
    """Stored component dicts -> ScoreComponents in a stable display order (JSONB loses key order)."""
    raw = raw or {}
    out = []
    for key, c in raw.items():
        if not isinstance(c, dict):
            continue
        out.append(ScoreComponent(
            key=c.get("key") or key, label=c.get("label") or COMPONENT_LABELS.get(key, key.replace("_", " ").capitalize()),
            value=float(c.get("value") or 0), max=float(c.get("max") or 0),
            explanation=c.get("explanation") or "", raw=c.get("raw") or {},
            evidence=[EvidenceItem.model_validate(e) for e in P.component_evidence(c)],
        ))
    rank = {k: i for i, k in enumerate(COMPONENT_ORDER)}
    return sorted(out, key=lambda c: (rank.get(c.key, len(rank)), -c.max, c.key))


def _evidence_items(items: Iterable[Any] | None, limit: int | None = None) -> list[EvidenceItem]:
    out = []
    for e in items or []:
        if isinstance(e, dict) and e.get("title"):
            try:
                out.append(EvidenceItem.model_validate(e))
            except ValueError:
                continue
        if limit and len(out) >= limit:
            break
    return out


def _prior_orgs(p: Person) -> list[str]:
    """A member's normalized prior organizations (the resolver's normalized copy when present)."""
    norm = ((p.attrs or {}).get("affiliations_normalized") or {}).get("prior_affiliations")
    if isinstance(norm, list) and norm:
        vals = [v for v in norm if isinstance(v, str) and v]
    else:
        vals = [n for n in (normalize_org(a) for a in (p.prior_affiliations or [])) if n]
    return list(dict.fromkeys(vals))


# Profile placeholders that mean "no affiliation" ("无" = none).
_NO_AFFILIATION = frozenset({"none", "n/a", "na", "null", "nil", "no", "无", "nothing", "self", "myself"})


def _member_affiliations(p: Person) -> list[str]:
    vals = [P.clean_label(p.current_company), *map(P.clean_label, reversed(p.prior_affiliations or []))]
    vals = [v.strip(" |") for v in vals if v and v.strip(" |").lower() not in _NO_AFFILIATION]
    return list(dict.fromkeys(v for v in vals if v))[:MEMBER_AFFILIATIONS]


def _anchor_rows(session: Session, cands: Sequence[FormationCandidate]) -> dict[uuid.UUID, Company | Person]:
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for c in cands:
        by_type[c.anchor_type].add(c.anchor_id)
    out: dict[uuid.UUID, Company | Person] = {}
    if by_type.get("company"):
        out.update({c.id: c for c in session.scalars(select(Company).where(Company.id.in_(by_type["company"])))})
    if by_type.get("person"):
        out.update({p.id: p for p in session.scalars(select(Person).where(Person.id.in_(by_type["person"])))})
    return out


def _entity_ref(ent: Company | Person | None, anchor_type: str, anchor_id: uuid.UUID, fallback: str) -> EntityRef:
    if ent is None:
        return EntityRef(type=anchor_type, id=str(anchor_id), slug=None, name=fallback)
    return EntityRef(type=anchor_type, id=str(ent.id), slug=ent.slug, name=P.display_name(ent).name)


# ── evidence-first card content (deterministic, from stored facts only) ──────
# Lab pedigree comes from verified commits (``Person.attrs['lab_contributions']``) — never employment.
# Traction numbers come from the stored ``Repository`` row (stars / forks / gh_created_at); when that row
# is missing the fact is omitted rather than estimated.


def _plural(n: int, word: str) -> str:
    return f"{n:,} {word}{'' if n == 1 else 's'}"


def _and(items: Sequence[str]) -> str:
    return items[0] if len(items) == 1 else f"{', '.join(items[:-1])} and {items[-1]}"


def member_pedigree(labs: Sequence[LabContribution]) -> str | None:
    """'sgl-project/sglang · 784 commits (verified)' — the strongest verified lab contribution."""
    if not labs:
        return None
    lc = labs[0]
    return f"{lc.full_name} \u00b7 {_plural(lc.commits, 'commit')} (verified)"


def person_pedigree_fact(labs: Sequence[LabContribution]) -> str | None:
    """'272 verified commits to google-deepmind/mujoco_menagerie and mujoco' (the strongest lab's repositories,
    summed), plus the next lab when there is one."""
    if not labs:
        return None
    org = labs[0].org.lower()
    same = [c for c in labs if c.org.lower() == org]
    total = sum(c.commits for c in same)
    names = [same[0].full_name, *(c.repo for c in same[1:])]
    if len(names) > 3:
        names = [*names[:2], f"{len(names) - 2} more {same[0].org} repositories"]
    fact = f"{total:,} verified commit{'' if total == 1 else 's'} to {_and(names)}"
    other = next((c for c in labs if c.org.lower() != org), None)
    if other is not None:
        fact += f" \u00b7 {other.commits:,} to {other.full_name}"
    return fact


def team_pedigree_fact(member_labs: Sequence[Sequence[LabContribution]]) -> str | None:
    """'2 members previously contributed to sgl-project/sglang (784 commits) and vllm-project/vllm (214 commits)
    — verified' (each verified member's strongest lab repository)."""
    tops = [labs[0] for labs in member_labs if labs]
    if not tops:
        return None
    by_repo: dict[str, list[LabContribution]] = defaultdict(list)
    for c in tops:
        by_repo[c.full_name].append(c)
    ranked = sorted(by_repo.items(), key=lambda kv: (-max(c.commits for c in kv[1]), kv[0].lower()))
    parts = []
    for name, cs in ranked[:3]:
        counts = sorted((c.commits for c in cs), reverse=True)
        parts.append(f"{name} ({' + '.join(f'{n:,}' for n in counts)} commit{'' if sum(counts) == 1 else 's'})")
    if len(ranked) > 3:
        parts.append(f"{len(ranked) - 3} more lab repositories")
    who = _plural(len(tops), "member")
    return f"{who} previously contributed to {_and(parts)} \u2014 verified"


def repo_counts(r: Repository | None) -> str | None:
    """'600★ · 60 forks' from the stored repository row; None when unknown or zero."""
    if r is None:
        return None
    stars, forks = int(r.stars or 0), int(r.forks or 0)
    bits = ([f"{stars:,}{STAR}"] if stars > 0 else []) + ([_plural(forks, "fork")] if forks > 0 else [])
    return " \u00b7 ".join(bits) or None


def repo_traction(r: Repository | None, today: date) -> str | None:
    """'600★ · 60 forks in 40 days' (age from gh_created_at; 'since Nov 2025' when older; no age if unknown)."""
    counts = repo_counts(r)
    if counts is None or r is None:
        return None
    if r.gh_created_at is not None:
        age = (today - r.gh_created_at.date()).days
        if 0 <= age <= RECENT_REPO_DAYS:
            counts += f" in {_plural(max(age, 1), 'day')}"
        elif age > RECENT_REPO_DAYS:
            counts += f" since {r.gh_created_at:%b %Y}"
    return counts


def org_fact(c: Company, repos: Sequence[Repository], n_members: int, n_verified: int, today: date) -> str | None:
    """'GitHub org created 138 days ago · 2 members, both with verified lab history' (None if the creation date
    is unknown)."""
    from radar.scoring.formation import company_created_info

    created, basis, _ = company_created_info(c, repos)
    if created is None:
        return None
    age = max((today - created).days, 0)
    what = ("First public repository" if basis == "first_public_repo_created_at"
            else "GitHub org" if basis == "github_org_created_at" else "Org")
    when = "today" if age == 0 else f"{_plural(age, 'day')} ago"
    fact = f"{what} created {when} \u00b7 {_plural(n_members, 'member')}"
    if n_verified:
        both = "both" if n_members == n_verified == 2 else "all" if n_members == n_verified else str(n_verified)
        fact += f", {both} with verified lab history" if n_members > 1 else " with verified lab history"
    return fact


def momentum_fact(metrics: dict[str, Any] | None, shown_stars: int = 0) -> str | None:
    """'+1,166★ · 17 new contributors in 30 days' from the stored technical-activity metrics. The star gain is
    left out when it is (nearly) all of a repository's stars already shown (a repository younger than 30 days)."""
    m = metrics or {}

    def num(k: str) -> int:
        try:
            return round(float(m.get(k) or 0))
        except (TypeError, ValueError):
            return 0

    stars, newc, commits = num("stars_30d"), num("new_contributors_30d"), num("commits_4w")
    if shown_stars and stars >= 0.9 * shown_stars:
        stars = 0
    bits = ([f"+{stars:,}{STAR}"] if stars > 0 else []) + ([_plural(newc, "new contributor")] if newc > 0 else [])
    if bits:
        return f"{' · '.join(bits)} in 30 days"
    return f"{_plural(commits, 'commit')} in the last 4 weeks" if commits > 0 else None


def _tech_metrics(c: FormationCandidate) -> dict[str, Any]:
    raw = ((c.components or {}).get("technical_activity") or {}).get("raw") or {}
    m = raw.get("metrics")
    return m if isinstance(m, dict) else {}


def _cited_signal_ids(c: FormationCandidate) -> set[uuid.UUID]:
    items = list(c.why_now or [])
    items += ((c.components or {}).get("formation_evidence") or {}).get("evidence") or []
    return {u for u in (_uuid(i.get("signal_id")) for i in items if isinstance(i, dict) and i.get("signal_id")) if u}


def headline_facts(c: FormationCandidate, *, anchor: Company | Person | None, member_labs: Sequence[Sequence[
                   LabContribution]], new_repos: Sequence[Repository], company_repos: Sequence[Repository],
                   today: date) -> list[tuple[str, str]]:
    """(kind, text) facts, most telling first: verified pedigree, traction, formation, momentum (<= 4)."""
    out: list[tuple[str, str]] = []
    if c.kind == "person":
        ped = person_pedigree_fact(member_labs[0] if member_labs else [])
        if ped:
            out.append(("pedigree", ped))
        for r in sorted(new_repos, key=lambda r: (-(r.stars or 0), r.full_name))[:NEW_REPO_FACTS]:
            t = repo_traction(r, today)
            if t:
                out.append(("traction", f"New repo {r.full_name}: {t}"))
    else:
        ped = team_pedigree_fact(member_labs)
        if ped:
            out.append(("pedigree", ped))
        if isinstance(anchor, Company):
            f = org_fact(anchor, company_repos, len(c.member_ids or []), sum(1 for x in member_labs if x), today)
            if f:
                out.append(("formation", f))
        top = max(company_repos, key=lambda r: (r.stars or 0, r.forks or 0, r.full_name), default=None)
        t = repo_traction(top, today)
        if top is not None and t:
            out.append(("traction", f"{top.full_name}: {t}"))
        mo = momentum_fact(_tech_metrics(c), int(top.stars or 0) if top is not None and t else 0)
        if mo:
            out.append(("momentum", mo))
    return out[:HEADLINE_FACTS]


_NEW_REPO_PHRASE = " created a new repository"


def enrich_why_now(items: list[EvidenceItem], kind: str, new_project: dict[uuid.UUID, str],
                   repos: dict[str, Repository]) -> list[EvidenceItem]:
    """Rewrite a new-repository line with its stored traction: 'Created kevinzakka/mjbatch — 600★ · 60 forks
    since' (the date column carries the creation date). Unchanged when the counts are not on record."""
    out = []
    for e in items:
        sid = _uuid(e.signal_id) if e.signal_id else None
        repo = new_project.get(sid) if sid else None
        counts = repo_counts(repos.get(repo.lower())) if repo else None
        if repo and counts:
            who = e.title.split(_NEW_REPO_PHRASE)[0] if _NEW_REPO_PHRASE in e.title and kind != "person" else None
            e = e.model_copy(update={"title": f"{f'{who} created' if who else 'Created'} {repo} \u2014 {counts} since"})
        out.append(e)
    return out


# ── cards ────────────────────────────────────────────────────────────────────


def formation_cards(session: Session, cands: Sequence[FormationCandidate], *,
                    full_age: bool = False) -> list[FormationCard]:
    """FormationCards for many candidates: members, anchors and member scores in one query each."""
    if not cands:
        return []
    member_ids = {m for c in cands for m in (c.member_ids or [])}
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(member_ids)))} if member_ids else {}
    scores = P.latest_scores(session, member_ids)
    anchors = _anchor_rows(session, cands)
    ages: dict[uuid.UUID, DataAge] = {}
    if full_age:
        for et in ("company", "person"):
            ids = [c.anchor_id for c in cands if c.anchor_type == et]
            if ids:
                ages.update(data_age(session, et, ids))
    labs = {pid: person_lab_contributions(p) for pid, p in people.items()}
    today = clock.today()
    # Company anchors' repositories (org age, top repository traction) — one query.
    company_ids = [c.anchor_id for c in cands if isinstance(anchors.get(c.anchor_id), Company)]
    company_repos: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    if company_ids:
        for r in session.scalars(select(Repository).where(Repository.company_id.in_(company_ids))):
            company_repos[r.company_id].append(r)  # type: ignore[index]
    # New-repository signals cited by the candidates (why now / formation evidence) -> repository name.
    cited = {sid for c in cands for sid in _cited_signal_ids(c)}
    new_project: dict[uuid.UUID, str] = {}
    if cited:
        for sid, meta in session.execute(select(Signal.id, Signal.meta).where(
                Signal.id.in_(cited), Signal.signal_type == "new_project")):
            repo = (meta or {}).get("repo")
            if isinstance(repo, str) and "/" in repo:
                new_project[sid] = repo
    wanted = {r.lower() for r in new_project.values()}
    for c in cands:
        repo = _tech_metrics(c).get("new_project_repo")
        if isinstance(repo, str) and "/" in repo:
            wanted.add(repo.lower())
    repos_by_name: dict[str, Repository] = {}
    if wanted:
        repos_by_name = {r.full_name.lower(): r for r in session.scalars(
            select(Repository).where(func.lower(Repository.full_name).in_(wanted)))}
    out = []
    for c in cands:
        members_rows = [people[m] for m in (c.member_ids or []) if m in people]
        members = [FormationMember(
            id=str(p.id), slug=p.slug, name=P.display_name(p).name, headline=P.headline(p), avatar_url=P.avatar(p),
            founder_score=round(scores[p.id]) if p.id in scores else None,
            affiliations=_member_affiliations(p), is_demo=p.is_demo,
            pedigree=member_pedigree(labs[p.id]), verified=bool(labs[p.id]),
        ) for p in members_rows]
        mine = _cited_signal_ids(c)
        names = {new_project[sid].lower() for sid in mine if sid in new_project}
        np_repo = _tech_metrics(c).get("new_project_repo")
        if isinstance(np_repo, str):
            names.add(np_repo.lower())
        facts = headline_facts(
            c, anchor=anchors.get(c.anchor_id), member_labs=[labs[p.id] for p in members_rows],
            new_repos=[repos_by_name[n] for n in sorted(names) if n in repos_by_name],
            company_repos=company_repos.get(c.anchor_id, []), today=today)
        prev: list[str] = []
        for p in members_rows:
            prev.extend(_prior_orgs(p))
        prev = list(dict.fromkeys(prev))
        anchor = anchors.get(c.anchor_id)
        age = ages.get(c.anchor_id) or (light_data_age(anchor) if anchor is not None else None)
        out.append(FormationCard(
            id=str(c.id), slug=c.slug, kind=c.kind if c.kind in ("team", "person") else "team", title=c.title,
            label=c.label, tier=c.tier if c.tier in TIERS else "early", score=round(c.score or 0),
            components=components_list(c.components), theme=c.theme,
            theme_label=P.theme_label(c.theme) if c.theme else None,
            subtopic_label=subtopic_label(c.theme, c.subtopic), members=members, previous_affiliations=prev,
            previous_affiliations_status="INFERRED",
            previous_affiliations_note=P.SELF_REPORTED_NOTE if prev else None,
            independent_signals=int(c.independent_signals or 0), families=list(c.signal_families or []),
            first_detected_at=c.first_detected_at, latest_activity_at=c.latest_activity_at,
            why_now=enrich_why_now(_evidence_items(c.why_now, WHY_NOW_LIMIT), c.kind, new_project, repos_by_name),
            anchor=_entity_ref(anchor, c.anchor_type, c.anchor_id, c.title), is_demo=c.is_demo, data_age=age,
            headline_facts=[t for _, t in facts], headline_fact_kinds=[k for k, _ in facts],
        ))
    return out


# ── feed ─────────────────────────────────────────────────────────────────────


def _filtered(session: Session, *, theme: str | None, include_demo: bool):
    q = select(FormationCandidate)
    if not include_demo:
        q = q.where(FormationCandidate.is_demo.is_(False))
    if theme:
        th, _, sub = theme.partition(".")
        q = q.where(FormationCandidate.theme == th)
        if sub:
            q = q.where(or_(FormationCandidate.subtopic == theme, FormationCandidate.subtopic == sub))
    return q


def formation_feed(session: Session, tier: str | None = None, theme: str | None = None,
                   include_demo: bool | None = None) -> FormationFeed:
    """Candidates grouped by tier, strongest first. ``counts`` are totals under the theme/demo filter
    (independent of ``tier``) so the page can show every tier's size."""
    include_demo = include_demo_default(include_demo)
    theme = E.validate_theme(theme)
    if tier is not None and tier not in TIERS:
        raise E.InvalidParam(f"Unknown tier {tier!r}. Valid tiers: {', '.join(TIERS)}")
    base = _filtered(session, theme=theme, include_demo=include_demo).subquery()
    counts = dict(session.execute(select(base.c.tier, func.count()).group_by(base.c.tier)).tuples().all())
    wanted = [tier] if tier else list(TIERS)
    # Top TIER_LIMIT per tier in one query (window function), strongest and most recent first.
    rn = func.row_number().over(
        partition_by=FormationCandidate.tier,
        order_by=(FormationCandidate.score.desc(), FormationCandidate.latest_activity_at.desc().nulls_last(),
                  FormationCandidate.slug)).label("rn")
    inner = (_filtered(session, theme=theme, include_demo=include_demo)
             .where(FormationCandidate.tier.in_(wanted)).add_columns(rn).subquery())
    cand = select(FormationCandidate).join(inner, inner.c.id == FormationCandidate.id).where(inner.c.rn <= TIER_LIMIT)
    rows = list(session.scalars(cand.order_by(FormationCandidate.score.desc(),
                                              FormationCandidate.latest_activity_at.desc().nulls_last(),
                                              FormationCandidate.slug)))
    by_tier: dict[str, list[FormationCard]] = defaultdict(list)
    for card in formation_cards(session, rows):
        by_tier[card.tier].append(card)
    a = feed_as_of(session)
    n = {t: int(counts.get(t, 0)) for t in TIERS}
    return FormationFeed(
        as_of=a.data_as_of, is_snapshot=a.is_snapshot,
        counts=FormationCounts(**n, total=sum(n.values())),
        high=by_tier.get("high", []), emerging=by_tier.get("emerging", []), early=by_tier.get("early", []),
    )


# ── detail ───────────────────────────────────────────────────────────────────


def resolve_candidate(session: Session, id_or_slug: str) -> FormationCandidate | None:
    uid = _uuid(id_or_slug)
    if uid is not None:
        row = session.get(FormationCandidate, uid)
        if row is not None:
            return row
    return session.scalar(select(FormationCandidate).where(FormationCandidate.slug == id_or_slug))


def convergence_strength(score: float) -> str:
    return "HIGH" if score >= 0.66 else "MEDIUM" if score >= 0.33 else "LOW"


def _convergence(c: FormationCandidate) -> Convergence:
    conv = c.convergence or {}
    score = float(conv.get("score") or 0)
    explanation = conv.get("explanation") or (
        "Single person — no team convergence measured." if c.kind == "person"
        else "No prior relationships between members on record.")
    return Convergence(score=round(score, 3), strength=convergence_strength(score), explanation=explanation,
                       prior_relationships=int(conv.get("prior_relationships") or 0),
                       families=list(conv.get("families") or []))


def _repositories(session: Session, c: FormationCandidate, anchor, members: Sequence[Person]) -> list[Repository]:
    """Team anchored on a company: its repositories. Person / no company repos: repositories the
    members own or contribute to, newest first (formation activity, not old famous projects)."""
    if isinstance(anchor, Company):
        repos = list(session.scalars(select(Repository).where(Repository.company_id == anchor.id)
                                     .order_by(Repository.stars.desc(), Repository.full_name).limit(REPO_LIMIT)))
        if repos:
            return repos
    mids = [p.id for p in members]
    handles = [p.github_handle.lower() for p in members if p.github_handle]
    if not mids:
        return []
    owned = and_(Repository.owner_type == "User", func.lower(Repository.owner).in_(handles)) if handles else None
    contributed = Repository.id.in_(select(RepoContributor.repository_id).where(RepoContributor.person_id.in_(mids)))
    cond = or_(contributed, owned) if owned is not None else contributed
    return list(session.scalars(select(Repository).where(cond)
                                .order_by(Repository.gh_created_at.desc().nulls_last(), Repository.stars.desc())
                                .limit(REPO_LIMIT)))


def _papers(session: Session, members: Sequence[Person]) -> list[ResearchPaper]:
    mids = [p.id for p in members]
    if not mids:
        return []
    pids = select(PaperAuthor.paper_id).where(PaperAuthor.person_id.in_(mids))
    return list(session.scalars(select(ResearchPaper).where(ResearchPaper.id.in_(pids))
                                .order_by(ResearchPaper.published_at.desc().nulls_last()).limit(PAPER_LIMIT)))


def candidate_relationships(session: Session, c: FormationCandidate, repo_ids: Iterable[uuid.UUID]
                            ) -> list[Relationship]:
    """Relationship rows among members, from members to the anchor / its repositories, plus any
    the convergence score cites."""
    members = set(c.member_ids or [])
    targets = members | {c.anchor_id} | set(repo_ids)
    cited = {u for u in (_uuid(x) for x in ((c.convergence or {}).get("relationship_ids") or [])) if u}
    conds = []
    if members:
        conds.append(and_(Relationship.src_id.in_(members), Relationship.dst_id.in_(targets)))
        conds.append(and_(Relationship.dst_id.in_(members), Relationship.src_id.in_(targets)))
    if cited:
        conds.append(Relationship.id.in_(cited))
    if not conds:
        return []
    order = case({k: i for i, k in enumerate(KIND_ORDER)}, value=Relationship.kind, else_=len(KIND_ORDER))
    return list(session.scalars(select(Relationship).where(or_(*conds))
                                .order_by(order, Relationship.last_seen_at.desc().nulls_last(), Relationship.id)
                                .limit(RELATIONSHIP_LIMIT)))


def relationship_outs(session: Session, rels: Sequence[Relationship]) -> list[RelationshipOut]:
    by_type: dict[str, set[uuid.UUID]] = defaultdict(set)
    for r in rels:
        by_type[r.src_type].add(r.src_id)
        by_type[r.dst_type].add(r.dst_id)
    person_ids = by_type.pop("person", set())
    refs = E.entity_refs(session, by_type)
    people = {p.id: p for p in session.scalars(select(Person).where(Person.id.in_(person_ids)))} if person_ids else {}

    def ref(t: str, i: uuid.UUID) -> EntityRef:
        if i in people:
            p = people[i]
            return EntityRef(type="person", id=str(i), slug=p.slug, name=P.display_name(p).name)
        r = refs.get(i)
        return EntityRef(type=t, id=str(i), slug=r.get("slug") if r else None, name=(r or {}).get("name") or t)

    return [RelationshipOut(
        id=str(r.id), kind=r.kind, epistemic="VERIFIED" if r.epistemic == "VERIFIED" else "INFERRED",
        src=ref(r.src_type, r.src_id), dst=ref(r.dst_type, r.dst_id), first_seen_at=r.first_seen_at,
        last_seen_at=r.last_seen_at, evidence=_evidence_items(r.evidence),
    ) for r in rels]


def _timeline_signals(session: Session, ids: Iterable[uuid.UUID]) -> list[Signal]:
    now = clock.now()
    rows = E.related_signal_rows(session, ids, since=now - timedelta(days=TIMELINE_DAYS),
                                 until=now + timedelta(days=1), include_repos=False)
    sids = {r.sid for r in rows}
    if not sids:
        return []
    return list(session.scalars(select(Signal).where(Signal.id.in_(sids))
                                .order_by(Signal.event_at.desc(), Signal.id).limit(TIMELINE_LIMIT)))


def _evidence_sources(card: FormationCard, rels: Sequence[RelationshipOut]) -> list[tuple[str, str, str]]:
    out: list[tuple[str, str, str]] = []
    items: list[EvidenceItem] = list(card.why_now)
    for comp in card.components:
        items.extend(comp.evidence)
    for r in rels:
        items.extend(r.evidence)
    for e in items:
        if e.source_url:
            out.append((e.source_url, e.title, e.source_type or ""))
    return out


def formation_detail(session: Session, id_or_slug: str) -> FormationDetail | None:
    c = resolve_candidate(session, id_or_slug)
    if c is None:
        return None
    card = formation_cards(session, [c], full_age=True)[0]
    member_ids = list(c.member_ids or [])
    members = list(session.scalars(select(Person).where(Person.id.in_(member_ids)))) if member_ids else []
    anchor = _anchor_rows(session, [c]).get(c.anchor_id)
    repos = _repositories(session, c, anchor, members)
    papers = _papers(session, members)
    rels = relationship_outs(session, candidate_relationships(session, c, [r.id for r in repos]))
    tl = _timeline_signals(session, {c.anchor_id, *member_ids, *(r.id for r in repos)})
    jobs = []
    if isinstance(anchor, Company):
        jobs = [{"id": str(j.id), "title": j.title, "department": j.department, "role_class": j.role_class,
                 "location": j.location, "url": j.url, "posted_at": j.posted_at, "first_seen_at": j.first_seen_at,
                 "ats": j.ats, "is_demo": j.is_demo}
                for j in session.scalars(select(Job).where(Job.company_id == anchor.id, Job.closed_at.is_(None))
                                         .order_by(Job.posted_at.desc().nulls_last(), Job.first_seen_at.desc()))]
    extra = _evidence_sources(card, rels)
    extra += [(j["url"], f"Job posting: {j['title']}", "jobs") for j in jobs if j["url"]][:5]
    extra += [(E.gh_url(r.full_name, r.is_demo), f"GitHub repository {r.full_name}", "github") for r in repos]
    return FormationDetail(
        card=card, convergence=_convergence(c), relationships=rels, timeline=E.timeline_events(tl),
        repositories=E.repo_cards(session, repos), papers=E.paper_cards(session, papers), jobs=jobs,
        sources=E._sources([], tl, extra),
    )


def formation_score(session: Session, id_or_slug: str) -> AntiScore | None:
    """FORMATION SCORE breakdown in the AntiScore shape (Why drawer)."""
    c = resolve_candidate(session, id_or_slug)
    if c is None:
        return None
    computed = c.day or (c.updated_at.date() if isinstance(c.updated_at, datetime) else clock.today())
    return AntiScore(total=round(c.score or 0), components=components_list(c.components),
                     version="formation", computed_on=computed)

"""Deterministic relationship graph: the single table every team/convergence feature reads.

``build(session)`` derives edges from stored facts only — no LLM, no fuzzy guessing:

==================  =====================  ===========================================  =========
kind                endpoints              source                                       epistemic
==================  =====================  ===========================================  =========
AUTHORED            person -> paper        paper_authors                                VERIFIED
COAUTHORED_WITH     person <-> person      two authors on one paper (<= 25 authors)     VERIFIED
CONTRIBUTED_TO      person -> repository   repo_contributors                            VERIFIED
CONTRIBUTED_WITH    person <-> person      both >= 3 commits to the same repository     VERIFIED
MEMBER_OF/FOUNDED   person -> company      company_people (FOUNDED when is_founder)     VERIFIED only with a
                                                                                        non-self-reported source
WORKED_WITH         person <-> person      same normalized current/prior affiliation    INFERRED
WORKED_WITH         person <-> person      both public members of the same GitHub org   VERIFIED
                                           (``Person.attrs['github_orgs']``)
==================  =====================  ===========================================  =========

Dates are evidence dates (paper date, first/last commit), never ingestion time; a shared
self-reported affiliation carries no date, so its ``first_seen_at`` is NULL.
Person-person edges are stored once with ``str(src_id) < str(dst_id)``.

WORKED_WITH is skipped for very common organizations (> ``GIANT_ORG_PEOPLE`` tracked people
list it — "both list Google" says little) unless the pair also co-contributed somewhere.

A shared *public* GitHub organization membership (as returned by GitHub's API) is an observed fact
about membership, so that WORKED_WITH evidence is VERIFIED; it says nothing about when either person
joined, so it carries no date. It is skipped for the young organizations that are formation anchors
themselves (sharing the anchor is not prior history) and for orgs with > ``GIANT_ORG_PEOPLE`` tracked
public members. A pair with both kinds of evidence keeps one WORKED_WITH edge, VERIFIED.

TALENT POOL: repositories tagged ``attrs['talent_source']`` are frontier-lab repositories; their
CONTRIBUTED_TO / CONTRIBUTED_WITH evidence says "N commits to org/repo" (verified public commit history,
never employment) and CONTRIBUTED_WITH between two talent-pool people is a *prior collaboration* dated by
their overlapping commit weeks. ``talent_signals`` then emits ``lab_alumni_cluster`` (>= 2 talent-pool
people with >= 3 commits each to the same non-lab young org/repo) and commit-based ``joined_young_org``.

Automation and AI coding-agent accounts (``providers.github.is_bot``) get no edges at all.

The build is idempotent: rows are upserted on (src_id, dst_id, kind) and rows of the kinds
built here that are no longer supported by the data are deleted.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from itertools import combinations
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from radar import clock
from radar.models.core import (
    Company,
    CompanyPerson,
    PaperAuthor,
    Person,
    Relationship,
    RepoContributor,
    Repository,
    ResearchPaper,
    Signal,
)
from radar.pipeline.aliases import normalize_org, org_key

log = logging.getLogger(__name__)

WORKED_WITH = "WORKED_WITH"
COAUTHORED_WITH = "COAUTHORED_WITH"
CONTRIBUTED_WITH = "CONTRIBUTED_WITH"
MEMBER_OF = "MEMBER_OF"
FOUNDED = "FOUNDED"
AUTHORED = "AUTHORED"
CONTRIBUTED_TO = "CONTRIBUTED_TO"
KINDS = (WORKED_WITH, COAUTHORED_WITH, CONTRIBUTED_WITH, MEMBER_OF, FOUNDED, AUTHORED, CONTRIBUTED_TO)
PERSON_PAIR_KINDS = frozenset({WORKED_WITH, COAUTHORED_WITH, CONTRIBUTED_WITH})

VERIFIED = "VERIFIED"
INFERRED = "INFERRED"

MIN_CO_COMMITS = 3  # both people need this many commits to a repo for CONTRIBUTED_WITH
MAX_REPO_PAIR_CONTRIBUTORS = 30  # only the top-N qualifying contributors of a huge repo form pairs
MAX_PAPER_AUTHORS = 25  # larger author lists (consortium papers) are too weak for pairwise edges
GIANT_ORG_PEOPLE = 40
MAX_EVIDENCE = 10

# Affiliation strings that are not an employer anyone "worked with" someone at.
NON_EMPLOYERS = frozenset({
    "independent", "freelance", "freelancer", "self employed", "self", "student", "none", "n a", "na",
    "home", "personal", "open source", "opensource", "stealth", "stealth startup", "stealth mode",
    "unemployed", "retired", "remote", "github", "private", "me", "myself", "hobbyist", "researcher",
    "phd student", "university", "startup", "my company", "looking for job", "looking for work",
})
_PROFILE_URL = re.compile(r"^https?://(www\.)?github\.com/[^/]+/?$", re.I)


def ordered_pair(a: uuid.UUID, b: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    """Canonical (src, dst) for symmetric person-person kinds: string-compare of the uuids."""
    return (a, b) if str(a) < str(b) else (b, a)


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _aware(d: datetime | date | None) -> datetime | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    return datetime.combine(d, time.min, tzinfo=UTC)


def is_self_reported_source(url: str | None) -> bool:
    """A membership whose only source is a GitHub *profile* (what the person typed) is self-reported."""
    return bool(url) and bool(_PROFILE_URL.match(url or ""))


def membership_epistemic(source_url: str | None, role: str | None) -> str:
    """VERIFIED only when backed by a non-self-reported source (a repo commit graph, a team page…)."""
    if not source_url or is_self_reported_source(source_url):
        return INFERRED
    return VERIFIED


def employer_key(name: str | None) -> tuple[str, str] | None:
    """(grouping key, display name) for an affiliation string, or None when it is not an employer."""
    n = normalize_org(name)
    if not n:
        return None
    k = org_key(n)
    if not k or k in NON_EMPLOYERS or len(k) < 2:
        return None
    return k, n


def keep_evidence(items: list[dict[str, Any]], limit: int = MAX_EVIDENCE) -> list[dict[str, Any]]:
    """Newest first, capped — but always keep the *oldest* dated items too: convergence asks whether a
    collaboration predates an anchor, and that answer lives in the earliest evidence."""
    ordered = sorted(items, key=lambda e: (e.get("event_at") or "", e.get("title") or ""), reverse=True)
    if len(ordered) <= limit:
        return ordered
    dated = [e for e in ordered if e.get("event_at")]
    oldest = dated[-(limit // 2):] if dated else []
    head = [e for e in ordered if e not in oldest][: limit - len(oldest)]
    return sorted(head + oldest, key=lambda e: (e.get("event_at") or "", e.get("title") or ""), reverse=True)


@dataclass
class _Edge:
    src_type: str
    src_id: uuid.UUID
    dst_type: str
    dst_id: uuid.UUID
    kind: str
    epistemic: str = VERIFIED
    first: datetime | None = None
    last: datetime | None = None
    weight: float = 0.0
    is_demo: bool = False
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def see(self, at: datetime | None, last: datetime | None = None) -> None:
        last = last or at
        if at is not None and (self.first is None or at < self.first):
            self.first = at
        if last is not None and (self.last is None or last > self.last):
            self.last = last

    def values(self) -> dict[str, Any]:
        ev = keep_evidence(self.evidence)
        return {
            "src_type": self.src_type, "src_id": self.src_id, "dst_type": self.dst_type, "dst_id": self.dst_id,
            "kind": self.kind, "epistemic": self.epistemic, "first_seen_at": self.first, "last_seen_at": self.last,
            "weight": float(self.weight), "evidence": ev, "is_demo": self.is_demo,
        }


class _Builder:
    def __init__(self) -> None:
        self.edges: dict[tuple[uuid.UUID, uuid.UUID, str], _Edge] = {}

    def edge(self, src_type: str, src: uuid.UUID, dst_type: str, dst: uuid.UUID, kind: str, **kw: Any) -> _Edge:
        if kind in PERSON_PAIR_KINDS:
            src, dst = ordered_pair(src, dst)
        key = (src, dst, kind)
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = _Edge(src_type, src, dst_type, dst, kind, **kw)
        return e


def _demo(people: dict[uuid.UUID, Person], *ids: uuid.UUID) -> bool:
    return any(bool(getattr(people.get(i), "is_demo", False)) for i in ids)


def _profile_url(p: Person) -> str | None:
    if p.github_handle:
        return f"https://github.com/{p.github_handle}"
    return p.personal_site or p.linkedin_url


def build(session: Session) -> dict[str, Any]:
    """Rebuild all relationships from stored facts. Returns per-kind counts."""
    from radar.providers.github import is_bot

    b = _Builder()
    all_people = list(session.scalars(select(Person)))
    bots = {p.id for p in all_people if p.github_handle and is_bot(p.github_handle)}
    people: dict[uuid.UUID, Person] = {p.id: p for p in all_people if p.id not in bots}

    # ── papers ──────────────────────────────────────────────────────────────
    papers: dict[uuid.UUID, ResearchPaper] = {}
    authors: dict[uuid.UUID, list[tuple[int, uuid.UUID]]] = defaultdict(list)
    for pa, paper in session.execute(
        select(PaperAuthor, ResearchPaper).join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
    ).all():
        papers[paper.id] = paper
        authors[paper.id].append((pa.position or 0, pa.person_id))
    for pid_paper, rows in authors.items():
        paper = papers[pid_paper]
        at = _aware(paper.published_at)
        url = paper.url or f"https://arxiv.org/abs/{paper.arxiv_id}"
        ev = {"title": paper.title, "detail": f"arXiv:{paper.arxiv_id}", "source_url": url,
              "source_type": "arxiv", "event_at": _iso(at), "paper_id": str(paper.id)}
        rows = [(pos, pid) for pos, pid in rows if pid not in bots]
        for pos, person_id in rows:
            e = b.edge("person", person_id, "paper", paper.id, AUTHORED,
                       is_demo=bool(paper.is_demo or getattr(people.get(person_id), "is_demo", False)))
            e.see(at)
            e.weight = 1.0
            e.evidence = [{**ev, "detail": f"arXiv:{paper.arxiv_id} · author #{pos + 1} of {len(rows)}"}]
        ids = sorted({p for _, p in rows}, key=str)
        if len(ids) > MAX_PAPER_AUTHORS:
            continue
        for a, c in combinations(ids, 2):
            e = b.edge("person", a, "person", c, COAUTHORED_WITH)
            e.is_demo = e.is_demo or bool(paper.is_demo) or _demo(people, a, c)
            e.see(at)
            e.weight += 1
            e.evidence.append({**ev, "detail": f"Co-authors of arXiv:{paper.arxiv_id}"})

    # ── repositories ────────────────────────────────────────────────────────
    repo_rows: dict[uuid.UUID, list[RepoContributor]] = defaultdict(list)
    repos: dict[uuid.UUID, Repository] = {}
    for rc, repo in session.execute(
        select(RepoContributor, Repository).join(Repository, Repository.id == RepoContributor.repository_id)
    ).all():
        repos[repo.id] = repo
        if rc.person_id not in bots:
            repo_rows[repo.id].append(rc)
    co_contrib: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for rid, rows in repo_rows.items():
        repo = repos[rid]
        url = f"https://github.com/{repo.full_name}"
        lab = lab_source(repo)
        for rc in rows:
            e = b.edge("person", rc.person_id, "repository", rid, CONTRIBUTED_TO,
                       is_demo=bool(repo.is_demo or getattr(people.get(rc.person_id), "is_demo", False)))
            e.see(_aware(rc.first_commit_at), _aware(rc.last_commit_at))
            e.weight = float(rc.contributions or 0)
            ev = {"title": repo.full_name,
                  "detail": f"{rc.contributions or 0} commits on the default branch",
                  "source_url": f"{url}/graphs/contributors", "source_type": "github",
                  "event_at": _iso(_aware(rc.first_commit_at)), "repo_id": str(rid)}
            if lab:
                ev["detail"] = (f"{rc.contributions or 0} commits to {repo.full_name}"
                                + _span_text(rc.first_commit_at, rc.last_commit_at)
                                + " (public commit history; not employment)")
                ev.update({"lab": lab.get("label"), "lab_org": lab.get("org"), "lab_tier": lab.get("tier"),
                           "last_commit_at": _iso(_aware(rc.last_commit_at))})
            e.evidence = [ev]
        strong = sorted((rc for rc in rows if (rc.contributions or 0) >= MIN_CO_COMMITS),
                        key=lambda rc: (-(rc.contributions or 0), str(rc.person_id)))[:MAX_REPO_PAIR_CONTRIBUTORS]
        for x, y in combinations(strong, 2):
            if x.person_id == y.person_id:
                continue
            fx, fy = _aware(x.first_commit_at), _aware(y.first_commit_at)
            lx, ly = _aware(x.last_commit_at), _aware(y.last_commit_at)
            # Overlap starts when the later of the two started; unknown if either date is unknown.
            start = max(fx, fy) if fx and fy else None
            end = min(lx, ly) if lx and ly else None
            e = b.edge("person", x.person_id, "person", y.person_id, CONTRIBUTED_WITH)
            e.is_demo = e.is_demo or bool(repo.is_demo) or _demo(people, x.person_id, y.person_id)
            e.see(start, end)
            e.weight += 1
            ev = {
                "title": repo.full_name,
                "detail": f"Both contributed: {x.contributions} and {y.contributions} commits",
                "source_url": f"{url}/graphs/contributors", "source_type": "github",
                "event_at": _iso(start), "repo_id": str(rid),
                "company_id": str(repo.company_id) if repo.company_id else None,
            }
            if lab:  # prior collaboration on a frontier-lab repository (overlapping commit spans when known)
                ev["detail"] = (f"Both contributed to {repo.full_name} ({lab.get('label')}): {x.contributions} and "
                                f"{y.contributions} commits" + (f"; overlapping {start.date()}–{end.date()}"
                                                                if start and end and start <= end else ""))
                ev.update({"lab": lab.get("label"), "lab_org": lab.get("org"), "prior_collaboration": True,
                           "last_commit_at": _iso(end)})
            e.evidence.append(ev)
            co_contrib.add(ordered_pair(x.person_id, y.person_id))

    # ── company membership ──────────────────────────────────────────────────
    companies: dict[uuid.UUID, Company] = {c.id: c for c in session.scalars(select(Company))}
    first_commit_at_company: dict[tuple[uuid.UUID, uuid.UUID], tuple[datetime | None, datetime | None]] = {}
    for rid, rows in repo_rows.items():
        cid = repos[rid].company_id
        if cid is None:
            continue
        for rc in rows:
            f, last = _aware(rc.first_commit_at), _aware(rc.last_commit_at)
            pf, pl = first_commit_at_company.get((rc.person_id, cid), (None, None))
            first_commit_at_company[(rc.person_id, cid)] = (
                min(d for d in (f, pf) if d) if (f or pf) else None,
                max(d for d in (last, pl) if d) if (last or pl) else None,
            )
    for cp in session.scalars(select(CompanyPerson)):
        c = companies.get(cp.company_id)
        if c is None or cp.person_id in bots:
            continue
        kind = FOUNDED if cp.is_founder else MEMBER_OF
        epi = membership_epistemic(cp.source_url, cp.role)
        e = b.edge("person", cp.person_id, "company", cp.company_id, kind, epistemic=epi,
                   is_demo=bool(c.is_demo or getattr(people.get(cp.person_id), "is_demo", False)))
        commit_first, commit_last = first_commit_at_company.get((cp.person_id, cp.company_id), (None, None))
        e.see(_aware(cp.start_at) or commit_first, _aware(cp.end_at) or commit_last)
        e.weight = float(cp.confidence or 1.0)
        role = cp.role or ("founder" if cp.is_founder else "member")
        basis = "commit graph" if role == "contributor" else ("listed source" if epi == VERIFIED else
                                                             "unverified listing")
        e.evidence = [{"title": f"{role.capitalize()} of {c.name}", "detail": f"Basis: {basis}",
                       "source_url": cp.source_url, "source_type": "github" if (cp.source_url or "").startswith(
                           "https://github.com") else ("demo" if c.is_demo else None),
                       "event_at": _iso(e.first)}]

    # ── shared affiliations (self-reported) ─────────────────────────────────
    by_org: dict[str, list[tuple[uuid.UUID, str, str, bool]]] = defaultdict(list)  # key -> (pid, raw, disp, prior)
    for p in people.values():
        if p.dismissed:
            continue
        seen: set[str] = set()
        for raw, prior in [(p.current_company, False), *((a, True) for a in (p.prior_affiliations or []))]:
            ek = employer_key(raw)
            if ek is None or ek[0] in seen:
                continue
            seen.add(ek[0])
            by_org[ek[0]].append((p.id, raw or "", ek[1], prior))
    for key, members in by_org.items():
        if len(members) < 2:
            continue
        giant = len(members) > GIANT_ORG_PEOPLE
        members.sort(key=lambda m: str(m[0]))
        disp = members[0][2]
        for (a, raw_a, _, prior_a), (c, raw_c, _, prior_c) in combinations(members, 2):
            if giant and ordered_pair(a, c) not in co_contrib:
                continue
            pa, pc = people[a], people[c]
            e = b.edge("person", a, "person", c, WORKED_WITH, epistemic=INFERRED,
                       is_demo=bool(pa.is_demo or pc.is_demo))
            e.weight = 1.0
            both_prior = prior_a and prior_c
            e.evidence = [
                {"title": f"{pa.name} lists {disp} ({'prior' if prior_a else 'current'}, self-reported)",
                 "detail": f"Raw affiliation: “{raw_a}”", "source_url": _profile_url(pa),
                 "source_type": "github" if pa.github_handle else None, "event_at": None,
                 "org": disp, "org_key": key, "prior": prior_a, "person_id": str(a)},
                {"title": f"{pc.name} lists {disp} ({'prior' if prior_c else 'current'}, self-reported)",
                 "detail": f"Raw affiliation: “{raw_c}”", "source_url": _profile_url(pc),
                 "source_type": "github" if pc.github_handle else None, "event_at": None,
                 "org": disp, "org_key": key, "prior": prior_c, "person_id": str(c)},
            ]
            if both_prior:
                e.weight = 1.5

    _public_org_edges(b, people, companies, repos)
    out = _persist(session, b)
    out.update(talent_signals(session, people, companies, repos, repo_rows))
    return out


def public_orgs_of(p: Person) -> list[dict[str, Any]]:
    """``Person.attrs['github_orgs']`` entries ({login, name, observed_at, ...}); [] when never checked."""
    return [o for o in ((p.attrs or {}).get("github_orgs") or []) if isinstance(o, dict) and o.get("login")]


def public_org_alias(o: dict[str, Any]) -> str | None:
    """Canonical organization for a public GitHub org — only when its whole login (or name) is a known alias in
    ``config/org_aliases.yaml`` (``openai`` -> OpenAI, ``google-deepmind`` -> Google DeepMind). Partial matches
    ("tesla-fans-club") are not trusted."""
    from radar.pipeline.aliases import alias_map

    amap = alias_map()
    for cand in (o.get("login"), o.get("name")):
        k = org_key(str(cand)) if cand else ""
        if k and k in amap:
            return amap[k]
    return None


def public_org_display(o: dict[str, Any]) -> str:
    """Display name for a public GitHub org: its canonical alias when known, else its name/login."""
    return public_org_alias(o) or str(o.get("name") or o.get("login"))


def anchor_org_logins(companies: dict[uuid.UUID, Company], repos: dict[uuid.UUID, Repository],
                      young_days: int | None = None) -> set[str]:
    """Lower-case logins of tracked young GitHub orgs (formation anchors): sharing one is not prior history."""
    from radar.scoring.formation import YOUNG_DAYS, _company_created

    by_company: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in repos.values():
        if r.company_id:
            by_company[r.company_id].append(r)
    today = clock.today()
    out: set[str] = set()
    for c in companies.values():
        if not c.github_org:
            continue
        created = _company_created(c, by_company.get(c.id, []))
        if created is not None and 0 <= (today - created).days <= (young_days or YOUNG_DAYS):
            out.add(c.github_org.lower())
    return out


def _public_org_edges(b: _Builder, people: dict[uuid.UUID, Person], companies: dict[uuid.UUID, Company],
                      repos: dict[uuid.UUID, Repository]) -> None:
    members: dict[str, list[tuple[uuid.UUID, dict[str, Any]]]] = defaultdict(list)
    for p in people.values():
        if p.dismissed:
            continue
        for o in public_orgs_of(p):
            members[str(o["login"]).lower()].append((p.id, o))
    if not members:
        return
    anchors = anchor_org_logins(companies, repos)
    for login, rows in members.items():
        if len(rows) < 2 or login in anchors or len(rows) > GIANT_ORG_PEOPLE:
            continue
        rows.sort(key=lambda r: str(r[0]))
        disp = public_org_display(rows[0][1])
        okey = org_key(disp) or login
        url = f"https://github.com/{rows[0][1]['login']}"
        for (a, oa), (c, oc) in combinations(rows, 2):
            if a == c:
                continue
            pa, pc = people[a], people[c]
            e = b.edge("person", a, "person", c, WORKED_WITH, epistemic=VERIFIED,
                       is_demo=bool(pa.is_demo or pc.is_demo))
            e.epistemic = VERIFIED  # also upgrades a pair that only had shared self-reported affiliations
            e.weight = max(e.weight, 1.0)
            e.evidence = [{
                "title": f"Both are public members of github.com/{rows[0][1]['login']}",
                "detail": f"{pa.name} and {pc.name}: public GitHub organization membership ({disp}); "
                          "membership dates are not public",
                "source_url": url, "source_type": "github", "event_at": None,
                "org": disp, "org_key": okey, "org_login": rows[0][1]["login"], "prior": False,
                "basis": "public_github_org", "person_ids": [str(a), str(c)],
                "observed_at": max(str(oa.get("observed_at") or ""), str(oc.get("observed_at") or "")) or None,
            }, *e.evidence]


# ── talent pool: lab alumni converging on a young org / repo ─────────────────

TALENT_MIN_COMMITS_HERE = 3  # commits to the young org/repo for a talent-pool person to count as a member
LAB_ALUMNI_MIN = 2


def lab_source(repo: Repository) -> dict[str, Any] | None:
    """The talent source of a frontier-lab repository (``attrs['talent_source']``), else None."""
    v = (repo.attrs or {}).get("talent_source")
    return v if isinstance(v, dict) else None


def _span_text(first: datetime | None, last: datetime | None) -> str:
    f, la = _aware(first), _aware(last)
    if f and la:
        return f" (weeks of {f.date()} to {la.date()})"
    if la:
        return f" (latest {la.date()})"
    return ""


def lab_contributions_of(p: Person) -> list[dict[str, Any]]:
    return [e for e in ((p.attrs or {}).get("lab_contributions") or []) if isinstance(e, dict) and e.get("repo")]


def _talent_lab_orgs(talent: dict[uuid.UUID, Person]) -> set[str]:
    orgs = {str(e.get("org") or e["repo"].split("/")[0]).lower() for p in talent.values() for e in lab_contributions_of(p)}
    try:
        from radar.providers.github import load_talent_config

        orgs |= set(load_talent_config().by_org())
    except Exception:  # noqa: BLE001 — a broken config never blocks the relationship build
        log.warning("talent_sources.yaml could not be read", exc_info=True)
    return orgs


def _insert_signal(session: Session, *, entity_type: str, entity_id: uuid.UUID, signal_type: str, title: str,
                   description: str, source_url: str | None, event_at: datetime, dedup: str, meta: dict[str, Any],
                   normalized_value: float, raw_value: float | None, is_demo: bool,
                   links: list[tuple[uuid.UUID, str]]) -> bool:
    import hashlib

    from radar.models.core import Signal, SignalEntity

    key = hashlib.sha256(dedup.encode()).hexdigest()
    sid = session.scalar(
        insert(Signal).values(
            id=uuid.uuid4(), entity_id=entity_id, entity_type=entity_type, signal_type=signal_type,
            source_type="github", source_url=source_url, title=title[:2000], description=description,
            raw_value=raw_value, normalized_value=normalized_value, detected_at=clock.now(), event_at=event_at,
            confidence=1.0, epistemic="FACT", dedup_key=key, is_demo=is_demo, meta=meta,
        ).on_conflict_do_nothing(index_elements=["dedup_key"]).returning(Signal.id))
    if sid is None:
        return False
    for eid, etype in links:
        if eid != entity_id:
            session.execute(insert(SignalEntity).values(id=uuid.uuid4(), signal_id=sid, entity_id=eid,
                                                        entity_type=etype, relation="related")
                            .on_conflict_do_nothing(constraint="uq_signal_entity"))
    return True


def talent_signals(session: Session, people: dict[uuid.UUID, Person], companies: dict[uuid.UUID, Company],
                   repos: dict[uuid.UUID, Repository], repo_rows: dict[uuid.UUID, list[RepoContributor]],
                   ) -> dict[str, int]:
    """Signals from the talent pool's commit graph (FACT; idempotent via dedup keys):

    * ``lab_alumni_cluster`` — >= 2 talent-pool people (verified frontier-lab commit history) with >= 3 commits
      each to the same *non-lab* young org (entity: company) or young repository without a tracked org (entity:
      repository; its owner counts as a member). A new member set is a new event.
    * ``joined_young_org`` — a talent-pool person with >= 3 commits to a young org's repositories (complements the
      public-membership check in ``GitHubProvider.recent_activity``; one per person and org)."""
    from radar.scoring.formation import YOUNG_DAYS, _company_created, curated_list_anchor

    talent = {pid: p for pid, p in people.items() if lab_contributions_of(p) and not p.dismissed}
    out = {"lab_alumni_cluster": 0, "talent_joined_young_org": 0, "talent_pool": len(talent)}
    if len(talent) < 1:
        return out
    lab_orgs = _talent_lab_orgs(talent)
    today = clock.today()
    by_company: dict[uuid.UUID, list[Repository]] = defaultdict(list)
    for r in repos.values():
        if r.company_id:
            by_company[r.company_id].append(r)
    young_company: dict[uuid.UUID, date] = {}
    for cid, c in companies.items():
        if c.dismissed or (c.github_org or "").lower() in lab_orgs:
            continue
        created = _company_created(c, by_company.get(cid, []))
        if created is not None and 0 <= (today - created).days <= YOUNG_DAYS \
                and curated_list_anchor(c, by_company.get(cid, [])) is None:
            young_company[cid] = created

    # anchor -> person -> {commits, first, repos}
    anchors: dict[tuple[str, uuid.UUID], dict[uuid.UUID, dict[str, Any]]] = defaultdict(dict)
    anchor_created: dict[tuple[str, uuid.UUID], date | None] = {}
    for rid, rows in repo_rows.items():
        repo = repos[rid]
        if lab_source(repo) or (repo.owner or "").lower() in lab_orgs:
            continue
        if repo.company_id and repo.company_id in young_company:
            anchor = ("company", repo.company_id)
            anchor_created[anchor] = young_company[repo.company_id]
        elif repo.company_id is None and repo.gh_created_at is not None \
                and 0 <= (today - _aware(repo.gh_created_at).date()).days <= YOUNG_DAYS \
                and not curated_list_anchor_repo(repo):
            anchor = ("repository", rid)
            anchor_created[anchor] = _aware(repo.gh_created_at).date()
        else:
            continue
        for rc in rows:
            p = talent.get(rc.person_id)
            if p is None:
                continue
            owner = anchor[0] == "repository" and p.github_handle and p.github_handle.lower() == (repo.owner or "").lower()
            if (rc.contributions or 0) < TALENT_MIN_COMMITS_HERE and not owner:
                continue
            m = anchors[anchor].setdefault(rc.person_id, {"commits": 0, "first": None, "repos": [], "owner": False})
            m["commits"] += int(rc.contributions or 0)
            m["owner"] = m["owner"] or bool(owner)
            f = _aware(rc.first_commit_at)
            if f and (m["first"] is None or f < m["first"]):
                m["first"] = f
            m["repos"].append(repo.full_name)

    existing_joined = {(eid, str((meta or {}).get("org") or "").lower()) for eid, meta in session.execute(
        select(Signal.entity_id, Signal.meta).where(Signal.signal_type == "joined_young_org",
                                                    Signal.entity_id.in_(list(talent)))).all()}
    for anchor, members in anchors.items():
        kind, aid = anchor
        created = anchor_created.get(anchor)
        created_at = _aware(created) if created else None
        if kind == "company":
            c = companies[aid]
            name, login = c.name, c.github_org
            url = f"https://github.com/{login}" if login else None
            # joined_young_org from commits (public membership is checked by the provider).
            for pid, m in members.items():
                if not login or m["commits"] < TALENT_MIN_COMMITS_HERE or (pid, login.lower()) in existing_joined:
                    continue
                p = talent[pid]
                age = (today - created).days if created else None
                if _insert_signal(
                    session, entity_type="person", entity_id=pid, signal_type="joined_young_org",
                    title=f"{p.github_handle or p.name} is committing to {name}, a GitHub organization created "
                          f"{age} days ago",
                    description=(f"{m['commits']} commits to {', '.join(sorted(set(m['repos'])))} (public commit "
                                 f"history) in github.com/{login}, created {created}. A young organization is a "
                                 "possible formation signal, not a company."),
                    source_url=url, event_at=m["first"] or created_at or clock.now(),
                    dedup=f"joined_young_org|{pid}|{login.lower()}",
                    meta={"org": login, "org_name": name, "org_created_at": _iso(created_at), "commits": m["commits"],
                          "repo": sorted(set(m["repos"]))[0], "public_member": False, "age_days": age,
                          "basis": "repo_contributors commit counts"},
                    normalized_value=0.75, raw_value=float(m["commits"]),
                    is_demo=bool(p.is_demo or c.is_demo), links=[(aid, "company")],
                ):
                    out["talent_joined_young_org"] += 1
                existing_joined.add((pid, login.lower()))
        else:
            r = repos[aid]
            name, url = r.full_name, f"https://github.com/{r.full_name}"
        if len(members) < LAB_ALUMNI_MIN:
            continue
        out["lab_alumni_cluster"] += 1
        rows_m = []
        for pid, m in sorted(members.items(), key=lambda kv: (-kv[1]["commits"], str(kv[0]))):
            p = talent[pid]
            labs = [{"org": e.get("org"), "label": e.get("label"), "tier": e.get("tier"), "repo": e.get("repo"),
                     "commits": e.get("commits")} for e in lab_contributions_of(p)][:5]
            rows_m.append({"person_id": str(pid), "login": p.github_handle, "name": p.name, "commits": m["commits"],
                           "owner": m["owner"], "repos": sorted(set(m["repos"])),
                           "first_commit_at": _iso(m["first"]), "labs": labs})
        firsts = sorted(m["first"] for m in members.values() if m["first"])
        event_at = firsts[1] if len(firsts) >= 2 else (created_at or clock.now())
        lab_names = sorted({str(lb.get("label") or lb.get("org")) for m in rows_m for lb in m["labs"]})
        tier1 = any(int(lb.get("tier") or 2) == 1 for m in rows_m for lb in m["labs"])
        who = "; ".join(
            f"{m['login'] or m['name']} ({m['commits']} commits here" + ("" if not m["owner"] else ", owner")
            + "; previously " + ", ".join(f"{lb['commits']} commits to {lb['repo']}" for lb in m["labs"][:2]) + ")"
            for m in rows_m[:8])
        demo = any(talent[pid].is_demo for pid in members) or (kind == "company" and companies[aid].is_demo) \
            or (kind == "repository" and repos[aid].is_demo)
        n = len(members)
        _insert_signal(
            session, entity_type=kind, entity_id=aid, signal_type="lab_alumni_cluster",
            title=f"{n} engineers with frontier-lab commit history are contributing to {name}",
            description=(f"{who}. Lab history is verified public commit history ({', '.join(lab_names)}), not "
                         "employment. Several lab alumni on one young project is a possible formation signal."),
            source_url=url, event_at=event_at,
            dedup=f"lab_alumni_cluster|{kind}|{aid}|" + ",".join(sorted(str(pid) for pid in members)),
            meta={"anchor_type": kind, "anchor": name, "anchor_created_at": _iso(created_at), "members": rows_m,
                  "lab_orgs": sorted({str(lb.get("org")) for m in rows_m for lb in m["labs"] if lb.get("org")}),
                  "labs": lab_names, "min_commits": TALENT_MIN_COMMITS_HERE, "basis": "repo_contributors + "
                  "Person.attrs.lab_contributions (GitHub commit history)"},
            normalized_value=round(min(1.0, 0.5 + 0.15 * (n - 1) + (0.1 if tier1 else 0.0)), 4), raw_value=float(n),
            is_demo=bool(demo), links=[(pid, "person") for pid in members],
        )
    return out


def curated_list_anchor_repo(repo: Repository) -> bool:
    from radar.providers.github import is_curated_list_repo

    return is_curated_list_repo(repo.full_name, repo.description, repo.topics or [])


def _persist(session: Session, b: _Builder) -> dict[str, Any]:
    now = clock.wall_now()
    rows = [e.values() for e in b.edges.values()]
    keep: set[tuple[uuid.UUID, uuid.UUID, str]] = set(b.edges)
    existing = {(s, d, k): rid for rid, s, d, k in session.execute(
        select(Relationship.id, Relationship.src_id, Relationship.dst_id, Relationship.kind)
        .where(Relationship.kind.in_(KINDS))
    ).all()}
    stale = [rid for key, rid in existing.items() if key not in keep]
    for i in range(0, len(stale), 1000):
        session.execute(delete(Relationship).where(Relationship.id.in_(stale[i:i + 1000])))
    for i in range(0, len(rows), 500):
        chunk = [{"id": uuid.uuid4(), **r} for r in rows[i:i + 500]]
        stmt = insert(Relationship).values(chunk)
        cols = ("src_type", "dst_type", "epistemic", "first_seen_at", "last_seen_at", "weight", "evidence", "is_demo")
        upd = {c: stmt.excluded[c] for c in cols}
        upd["updated_at"] = now
        session.execute(stmt.on_conflict_do_update(constraint="uq_relationship", set_=upd))
    session.flush()
    counts: dict[str, int] = defaultdict(int)
    for e in b.edges.values():
        counts[e.kind] += 1
    out = {k: counts.get(k, 0) for k in KINDS}
    out["deleted"] = len(stale)
    log.info("relationships.build %s", out)
    return out


__all__ = ["KINDS", "PERSON_PAIR_KINDS", "build", "employer_key", "lab_contributions_of", "lab_source",
           "membership_epistemic", "ordered_pair", "public_org_alias", "public_org_display", "public_orgs_of",
           "talent_signals"]

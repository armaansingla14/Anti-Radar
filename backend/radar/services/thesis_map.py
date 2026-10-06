"""Thesis Map: every theme/subcategory of the Anti Fund thesis with deterministic momentum/attention.

Per cell (topic ``theme.subcategory``):

* ``companies``          companies with an EntityTopic score >= 0.3 on the topic (not dismissed)
* ``emerging_companies`` of those, stage formation/pre_seed, kind stealth/project, or first seen <= 45 days ago
* ``founders``           people tagged with the topic
* ``papers_30d``         papers tagged with the topic, published in the last 30 days
* ``hiring_roles_30d``   technical jobs at linked companies first seen in the last 30 days
* ``momentum``           100 x (0.7 x mean component share of the top-5 linked companies'
                         technical_momentum + acceleration, + 0.3 x signal growth share), where
                         growth share = signals(last 14d) / (signals(last 14d) + signals(prior 14d))
                         (0.5 = flat; no signals at all -> 0.5 is replaced by 0)
* ``attention``          mean attention percentile of linked companies' latest score snapshots
* ``attention_gap``      momentum >= 60 and attention <= 40

Theme momentum is the mean of its cells weighted by (1 + companies), so empty cells count little.
Everything is batched: a fixed number of queries regardless of thesis size.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from radar import clock
from radar.detectors.hiring import is_technical_class
from radar.models import Company, EntityTopic, Job, Person, ResearchPaper, Signal, Topic
from radar.schemas.api import ThesisCell, ThesisMap, ThesisTheme
from radar.services.presenters import latest_snapshots
from radar.thesis.loader import Thesis, get_thesis

MIN_TOPIC_SCORE = 0.3
EMERGING_DAYS = 45
EMERGING_STAGES = frozenset({"formation", "pre_seed"})
EMERGING_KINDS = frozenset({"stealth", "project"})
GROWTH_WINDOW = 14
TOP_N_MOMENTUM = 5
COMPONENT_WEIGHT = 0.7
GROWTH_WEIGHT = 0.3
GAP_MOMENTUM_MIN = 60.0
GAP_ATTENTION_MAX = 40.0


def _dt(d) -> datetime:
    return datetime.combine(d, time.max, tzinfo=UTC)


def growth_trend(recent: int, prior: int) -> str:
    """up2 = at least doubled (or >= 3 new from zero); up = +25%; down = -25%; else flat."""
    if prior == 0:
        if recent >= 3:
            return "up2"
        return "up" if recent > 0 else "flat"
    g = (recent - prior) / prior
    if g >= 1.0:
        return "up2"
    if g >= 0.25:
        return "up"
    if g <= -0.25:
        return "down"
    return "flat"


def attention_trend(now: float | None, before: float | None) -> str:
    if now is None or before is None:
        return "flat"
    d = now - before
    if d >= 15:
        return "up2"
    if d >= 5:
        return "up"
    if d <= -5:
        return "down"
    return "flat"


def momentum_share(components: dict[str, Any] | None) -> float | None:
    """(technical_momentum + acceleration) / (their max) from a score snapshot, 0..1."""
    comps = components or {}
    v = m = 0.0
    for key in ("technical_momentum", "acceleration"):
        c = comps.get(key) or {}
        if c.get("max"):
            v += float(c.get("value") or 0.0)
            m += float(c["max"])
    return v / m if m else None


def thesis_map(session: Session, thesis: Thesis | None = None) -> ThesisMap:
    thesis = thesis or get_thesis()
    today = clock.today()
    now_dt = _dt(today)

    # topic memberships (one query)
    rows = session.execute(
        select(Topic.key, EntityTopic.entity_type, EntityTopic.entity_id, EntityTopic.score)
        .join(Topic, Topic.id == EntityTopic.topic_id)
        .where(EntityTopic.score >= MIN_TOPIC_SCORE)
    ).all()
    members: dict[str, dict[str, dict[uuid.UUID, float]]] = defaultdict(lambda: defaultdict(dict))
    for key, etype, eid, score in rows:
        bucket = members[key][etype]
        bucket[eid] = max(bucket.get(eid, 0.0), float(score))

    company_ids = {e for k in members.values() for e in k.get("company", {})}
    person_ids = {e for k in members.values() for e in k.get("person", {})}
    paper_ids = {e for k in members.values() for e in k.get("paper", {})}

    companies = {c.id: c for c in session.scalars(
        select(Company).where(Company.id.in_(company_ids), Company.dismissed.is_(False)))} if company_ids else {}
    people = {p.id: p for p in session.scalars(
        select(Person).where(Person.id.in_(person_ids), Person.dismissed.is_(False)))} if person_ids else {}
    paper_pub = dict(session.execute(
        select(ResearchPaper.id, ResearchPaper.published_at).where(ResearchPaper.id.in_(paper_ids))
    ).tuples().all()) if paper_ids else {}

    # score snapshots now and 14 days ago (attention trend)
    snaps = latest_snapshots(session, list(companies) + list(people), today)
    snaps_before = latest_snapshots(session, list(companies), today - timedelta(days=GROWTH_WINDOW))

    # jobs first seen in 30d at linked companies
    jobs_by_company: dict[uuid.UUID, int] = defaultdict(int)
    if companies:
        for cid, rc in session.execute(
            select(Job.company_id, Job.role_class).where(
                Job.company_id.in_(list(companies)), Job.first_seen_at > now_dt - timedelta(days=30),
                Job.first_seen_at <= now_dt)
        ):
            if is_technical_class(rc):
                jobs_by_company[cid] += 1

    # signal counts per entity: last 14d vs prior 14d (one grouped query)
    all_ids = list(companies) + list(people) + list(paper_ids)
    recent_lo = now_dt - timedelta(days=GROWTH_WINDOW)
    prior_lo = now_dt - timedelta(days=2 * GROWTH_WINDOW)
    sig_recent: dict[uuid.UUID, int] = defaultdict(int)
    sig_prior: dict[uuid.UUID, int] = defaultdict(int)
    if all_ids:
        is_recent = Signal.event_at > recent_lo
        for eid, recent, n in session.execute(
            select(Signal.entity_id, is_recent, func.count())
            .where(and_(Signal.entity_id.in_(all_ids), Signal.event_at > prior_lo, Signal.event_at <= now_dt))
            .group_by(Signal.entity_id, is_recent)
        ):
            (sig_recent if recent else sig_prior)[eid] += int(n)

    emerging_cut = now_dt - timedelta(days=EMERGING_DAYS)
    papers_cut = now_dt - timedelta(days=30)

    themes: list[ThesisTheme] = []
    for tkey, theme in thesis.themes.items():
        cells: list[ThesisCell] = []
        for sub in theme.subcategories.values():
            m = members.get(sub.key, {})
            cids = [c for c in m.get("company", {}) if c in companies]
            pids = [p for p in m.get("person", {}) if p in people]
            papers = [p for p in m.get("paper", {}) if paper_pub.get(p) and papers_cut < paper_pub[p] <= now_dt]
            emerging = sum(
                1 for c in cids
                if companies[c].stage in EMERGING_STAGES or companies[c].kind in EMERGING_KINDS
                or (companies[c].first_seen_at and companies[c].first_seen_at >= emerging_cut)
            )
            # momentum: component share of the top-5 linked companies by score
            ranked = sorted((c for c in cids if c in snaps), key=lambda c: -snaps[c].score)[:TOP_N_MOMENTUM]
            shares = [s for s in (momentum_share(snaps[c].components) for c in ranked) if s is not None]
            comp_share = sum(shares) / len(shares) if shares else 0.0
            linked = cids + pids + list(m.get("paper", {}))
            recent = sum(sig_recent.get(e, 0) for e in linked)
            prior = sum(sig_prior.get(e, 0) for e in linked)
            growth_share = recent / (recent + prior) if (recent + prior) else 0.0
            momentum = round(100 * (COMPONENT_WEIGHT * comp_share + GROWTH_WEIGHT * growth_share), 1)

            att_now = [float((snaps[c].attention or {}).get("attention")) for c in cids
                       if c in snaps and (snaps[c].attention or {}).get("attention") is not None]
            att_before = [float((snaps_before[c].attention or {}).get("attention")) for c in cids
                          if c in snaps_before and (snaps_before[c].attention or {}).get("attention") is not None]
            attention = round(sum(att_now) / len(att_now), 1) if att_now else None
            a_trend = attention_trend(
                attention if att_now else None, (sum(att_before) / len(att_before)) if att_before else None)

            top: list[dict[str, Any]] = []
            # Company and founder scores are different scales: companies first, then people.
            ranked = sorted((e for e in cids if e in snaps), key=lambda e: -snaps[e].score)
            ranked += sorted((e for e in pids if e in snaps), key=lambda e: -snaps[e].score)
            for eid in ranked[:3]:
                ent = companies.get(eid) or people.get(eid)
                top.append({"type": "company" if eid in companies else "person", "id": str(eid), "slug": ent.slug,
                            "name": ent.name, "score": round(snaps[eid].score), "is_demo": ent.is_demo})
            cells.append(ThesisCell(
                key=sub.key, label=sub.label, theme=tkey, momentum=momentum,
                momentum_trend=growth_trend(recent, prior), attention=attention, attention_trend=a_trend,
                attention_gap=attention is not None and momentum >= GAP_MOMENTUM_MIN and attention <= GAP_ATTENTION_MAX,
                companies=len(cids), emerging_companies=emerging, founders=len(pids), papers_30d=len(papers),
                hiring_roles_30d=sum(jobs_by_company.get(c, 0) for c in cids), top_entities=top,
            ))
        weights = [1 + c.companies for c in cells]
        tm = sum(c.momentum * w for c, w in zip(cells, weights, strict=True)) / sum(weights) if cells else 0.0
        themes.append(ThesisTheme(key=tkey, label=theme.label, weight=theme.weight, momentum=round(tm, 1),
                                  cells=cells))
    return ThesisMap(themes=themes, generated_at=clock.now())

"""Deterministic thesis classification (keyword matching against the thesis YAML).

Runs before any LLM call and is fully explainable: every EntityTopic written here
carries the matched keywords as evidence. Scores saturate: ``1 - exp(-hits / k)``.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import delete, select, true
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from radar.models import (
    Company,
    EntityTopic,
    PaperAuthor,
    Person,
    RepoContributor,
    Repository,
    ResearchPaper,
    Topic,
)
from radar.thesis.loader import Thesis

SATURATION_K = 2.0
MIN_SCORE = 0.3
MAX_THEME_TAGS = 3
TAG_MIN_SCORE = 0.35

# Text weights per field.
W_TOPICS = 2.0
W_DESCRIPTION = 1.5
W_TITLE = 1.0
W_BODY = 0.6
W_ABSTRACT = 0.5

# arXiv category priors. Subcategory keys add hits directly; theme keys only
# reinforce subcategories of that theme that already matched by keyword.
CATEGORY_PRIORS: dict[str, dict[str, float]] = {
    "cs.RO": {"robotics": 0.75},
    "eess.SY": {"robotics": 0.4},
    "cs.AR": {"semiconductors": 0.75},
    "cs.DC": {"frontier_infrastructure": 0.5},
    "cs.OS": {"frontier_infrastructure.systems": 0.6},
    "cs.NI": {"frontier_infrastructure": 0.4},
    "cs.LG": {"ai": 0.4},
    "cs.AI": {"ai": 0.4},
    "cs.CL": {"ai": 0.4},
    "quant-ph": {"frontier_infrastructure.quantum": 0.8},
    "physics.plasm-ph": {"energy.fusion": 0.8},
}

# Keywords whose meaning flips with the preceding word ("sensor fusion" is not fusion energy).
_NEGATIVE_PRECEDERS: dict[str, tuple[str, ...]] = {
    "fusion": ("sensor", "kernel", "operator", "feature", "data", "late", "early", "multimodal", "multi-modal",
               "layer", "op"),
    "transformer": ("power", "solid-state", "distribution", "voltage", "step-up", "step-down"),
}


@dataclass
class TopicMatch:
    key: str
    theme: str
    label: str
    score: float
    matched: list[str] = field(default_factory=list)


def _keyword_weight(kw: str) -> float:
    """Phrases are specific; short single tokens (acronyms) are ambiguous."""
    if re.search(r"[\s\-]", kw):
        return 1.0
    return 0.7 if len(kw) >= 5 else 0.5


@lru_cache(maxsize=2048)
def _keyword_re(kw: str) -> re.Pattern[str]:
    tokens = [re.escape(t) for t in re.split(r"[\s\-_]+", kw.lower().strip()) if t]
    last = tokens[-1]
    if last.endswith("y") and len(last) > 2 and last[-2] not in "aeiou":
        last = last[:-1] + "(?:y|ies)"
    else:
        last += "(?:s|es)?"
    body = r"[\s\-_]+".join([*tokens[:-1], last])
    neg = ""
    if kw.lower() in _NEGATIVE_PRECEDERS:
        neg = "".join(rf"(?<!{re.escape(p)} )(?<!{re.escape(p)}-)" for p in _NEGATIVE_PRECEDERS[kw.lower()])
    return re.compile(rf"{neg}(?<![a-z0-9]){body}(?![a-z0-9])")


def classify_text(
    texts: list[tuple[str, float]],
    thesis: Thesis,
    *,
    priors: dict[str, float] | None = None,
    min_score: float = MIN_SCORE,
) -> list[TopicMatch]:
    """Score every thesis subcategory against weighted texts; best first.

    Each distinct keyword found in a text adds ``weight * keyword_weight`` hits
    (repeat occurrences add 25% each, at most two extra). `priors` maps a
    subcategory key -> extra hits, or a theme key -> hits added only to
    subcategories of that theme that matched by keyword.
    """
    prepared = [(t.lower(), w) for t, w in texts if t and w > 0]
    hits: dict[str, float] = defaultdict(float)
    matched: dict[str, list[str]] = defaultdict(list)
    subs = {sub.key: (theme, sub) for theme in thesis.themes.values() for sub in theme.subcategories.values()}
    for key, (_theme, sub) in subs.items():
        for kw in sub.keywords:
            pat = _keyword_re(kw)
            kw_hits = 0.0
            for text, w in prepared:
                n = len(pat.findall(text))
                if n:
                    kw_hits += w * _keyword_weight(kw) * (1 + 0.25 * min(n - 1, 2))
            if kw_hits:
                hits[key] += kw_hits
                matched[key].append(kw)
    for pkey, extra in (priors or {}).items():
        if pkey in subs:
            hits[pkey] += extra
            matched[pkey].append(f"prior:{pkey}")
        elif pkey in thesis.themes:
            for key in list(matched):
                if key.startswith(pkey + ".") and any(not m.startswith("prior:") for m in matched[key]):
                    hits[key] += extra
                    matched[key].append(f"prior:{pkey}")
    out = []
    for key, h in hits.items():
        score = 1 - math.exp(-h / SATURATION_K)
        if score >= min_score:
            theme, sub = subs[key]
            out.append(TopicMatch(key=key, theme=theme.key, label=sub.label, score=round(score, 4),
                                  matched=matched[key]))
    out.sort(key=lambda m: (-m.score, m.key))
    return out


def category_priors(categories: list[str] | None) -> dict[str, float]:
    priors: dict[str, float] = defaultdict(float)
    for cat in categories or []:
        for k, v in CATEGORY_PRIORS.get(cat, {}).items():
            priors[k] = max(priors[k], v)
    return dict(priors)


def ensure_topics(session: Session, thesis: Thesis) -> dict[str, uuid.UUID]:
    """Upsert one Topic row per thesis subcategory. Returns key -> topic id."""
    for theme in thesis.themes.values():
        for sub in theme.subcategories.values():
            stmt = pg_insert(Topic).values(id=uuid.uuid4(), key=sub.key, theme=theme.key, label=sub.label)
            session.execute(stmt.on_conflict_do_update(
                index_elements=["key"], set_={"theme": stmt.excluded.theme, "label": stmt.excluded.label}))
    return dict(session.execute(select(Topic.key, Topic.id)).tuples().all())


# ── entity text builders ─────────────────────────────────────────────────────


def _repo_texts(repo_rows, *, topics_w: float, desc_w: float, readme_w: float) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for desc, topics, readme in repo_rows:
        if desc:
            out.append((desc, desc_w))
        if topics:
            out.append((" ; ".join(topics), topics_w))
        if readme and readme_w:
            out.append((readme[:4000], readme_w))
    return out


def _entity_texts(
    session: Session, etype: str, ids: list[uuid.UUID] | None
) -> dict[uuid.UUID, tuple[list[tuple[str, float]], dict[str, float]]]:
    """entity id -> (weighted texts, priors). Bulk queries per entity type."""
    texts: dict[uuid.UUID, list[tuple[str, float]]] = defaultdict(list)
    priors: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)

    def flt(col):
        return col.in_(ids) if ids is not None else true()

    if etype == "company":
        for cid, name, desc in session.execute(
            select(Company.id, Company.name, Company.description).where(flt(Company.id))
        ):
            texts[cid] += [(name, W_TITLE)] + ([(desc, W_DESCRIPTION)] if desc else [])
        for cid, desc, topics, readme in session.execute(
            select(Repository.company_id, Repository.description, Repository.topics, Repository.readme_excerpt)
            .where(Repository.company_id.is_not(None), flt(Repository.company_id))
        ):
            if cid in texts:
                texts[cid] += _repo_texts([(desc, topics, readme)], topics_w=W_TOPICS, desc_w=W_TITLE,
                                          readme_w=W_BODY)
        for cid, title in session.execute(
            select(ResearchPaper.company_id, ResearchPaper.title)
            .where(ResearchPaper.company_id.is_not(None), flt(ResearchPaper.company_id))
        ):
            if cid in texts:
                texts[cid].append((title, W_TITLE))
    elif etype == "person":
        for pid, bio in session.execute(select(Person.id, Person.bio).where(flt(Person.id))):
            texts[pid] += [(bio, W_DESCRIPTION)] if bio else []
        for pid, desc, topics in session.execute(
            select(RepoContributor.person_id, Repository.description, Repository.topics)
            .join(Repository, Repository.id == RepoContributor.repository_id)
            .where(flt(RepoContributor.person_id))
        ):
            if pid in texts:
                texts[pid] += _repo_texts([(desc, topics, None)], topics_w=W_TITLE, desc_w=W_BODY, readme_w=0)
        for pid, title, abstract in session.execute(
            select(PaperAuthor.person_id, ResearchPaper.title, ResearchPaper.abstract)
            .join(ResearchPaper, ResearchPaper.id == PaperAuthor.paper_id)
            .where(flt(PaperAuthor.person_id))
        ):
            if pid in texts:
                texts[pid].append((title, W_TITLE))
                if abstract:
                    texts[pid].append((abstract, W_ABSTRACT))
    elif etype == "repository":
        for rid, name, desc, topics, readme in session.execute(
            select(Repository.id, Repository.full_name, Repository.description, Repository.topics,
                   Repository.readme_excerpt).where(flt(Repository.id))
        ):
            texts[rid] = [(name.split("/")[-1].replace("-", " ").replace("_", " "), W_BODY)]
            texts[rid] += _repo_texts([(desc, topics, readme)], topics_w=W_TOPICS, desc_w=W_DESCRIPTION,
                                      readme_w=W_BODY)
    elif etype == "paper":
        for pid, title, abstract, cats in session.execute(
            select(ResearchPaper.id, ResearchPaper.title, ResearchPaper.abstract, ResearchPaper.categories)
            .where(flt(ResearchPaper.id))
        ):
            texts[pid] = [(title, W_DESCRIPTION)] + ([(abstract, W_ABSTRACT)] if abstract else [])
            priors[pid] = category_priors(cats)
    return {eid: (t, priors.get(eid, {})) for eid, t in texts.items()}


def classify_entities(
    session: Session,
    thesis: Thesis,
    entity_types: tuple[str, ...] = ("company", "person", "repository", "paper"),
    only_ids: list[uuid.UUID] | None = None,
) -> dict[str, int]:
    """Rewrite keyword EntityTopics (llm/manual rows are kept) and refresh theme tags.

    Returns counts: '<type>' = entities classified with >=1 topic, 'entity_topics' = rows written.
    """
    topic_ids = ensure_topics(session, thesis)
    topic_meta = {tid: key for key, tid in topic_ids.items()}
    counts: dict[str, int] = {"entity_topics": 0}
    for etype in entity_types:
        entities = _entity_texts(session, etype, only_ids)
        counts[etype] = 0
        if not entities:
            continue
        ids = list(entities)
        for i in range(0, len(ids), 1000):
            session.execute(delete(EntityTopic).where(
                EntityTopic.entity_id.in_(ids[i:i + 1000]), EntityTopic.method == "keyword"))
        rows = []
        for eid, (texts, priors) in entities.items():
            matches = classify_text(texts, thesis, priors=priors)
            if matches:
                counts[etype] += 1
            rows += [
                {"id": uuid.uuid4(), "entity_type": etype, "entity_id": eid, "topic_id": topic_ids[m.key],
                 "score": m.score, "method": "keyword", "evidence": m.matched[:20]}
                for m in matches
            ]
        for i in range(0, len(rows), 1000):
            inserted = session.scalars(pg_insert(EntityTopic).values(rows[i:i + 1000])
                                       .on_conflict_do_nothing(constraint="uq_entity_topic")
                                       .returning(EntityTopic.id)).all()
            counts["entity_topics"] += len(inserted)
        if etype in ("company", "person"):
            _refresh_theme_tags(session, etype, ids, topic_meta)
    return counts


def _refresh_theme_tags(session: Session, etype: str, ids: list[uuid.UUID], topic_meta: dict) -> None:
    """Company.thesis_tags / Person.domains := top themes across all EntityTopic methods."""
    best: dict[uuid.UUID, dict[str, float]] = defaultdict(dict)
    for i in range(0, len(ids), 1000):
        for eid, tid, score in session.execute(
            select(EntityTopic.entity_id, EntityTopic.topic_id, EntityTopic.score)
            .where(EntityTopic.entity_id.in_(ids[i:i + 1000]))
        ):
            theme = topic_meta.get(tid, "").split(".")[0]
            if theme and score >= TAG_MIN_SCORE:
                best[eid][theme] = max(best[eid].get(theme, 0.0), score)
    model, attr = (Company, "thesis_tags") if etype == "company" else (Person, "domains")
    for i in range(0, len(ids), 1000):
        for ent in session.scalars(select(model).where(model.id.in_(ids[i:i + 1000]))):
            ranked = sorted(best.get(ent.id, {}).items(), key=lambda kv: (-kv[1], kv[0]))
            tags = [t for t, _ in ranked[:MAX_THEME_TAGS]]
            if list(getattr(ent, attr) or []) != tags:
                setattr(ent, attr, tags)

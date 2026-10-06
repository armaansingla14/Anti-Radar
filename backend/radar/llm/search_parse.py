"""LLM-assisted query parsing and topic refinement (fast tier, cached).

Both helpers return ``None`` when no model (and no cached answer) is available so
callers can keep their deterministic result. Model output is never trusted
blindly: themes, topics, role classes and stages are filtered to the values
defined in the thesis config.
"""

from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, ConfigDict

from radar.llm.guard import SYSTEM_GUARD, wrap_source
from radar.llm.prompts import load_prompt
from radar.llm.provider import BaseProvider, LLMError, get_llm
from radar.schemas.api import SearchFilters
from radar.thesis.loader import get_thesis

log = logging.getLogger(__name__)

PROMPT_VERSION = "v1"


class _ParsedQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_types: list[Literal["company", "person", "repository", "paper"]]
    themes: list[str]
    topics: list[str]
    keywords: list[str]
    prior_affiliations: list[str]
    hiring_role_classes: list[str]
    stage: list[str]
    min_score: int
    first_seen_within_days: int
    event_within_days: int
    accelerating: bool
    sort: Literal["score", "momentum", "attention_gap", "recent", "delta"]


class _TopicScore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    score: float


class _TopicOut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topics: list[_TopicScore]


def _positive(v: int) -> int | None:
    return v if v > 0 else None


def llm_parse_query(query: str, *, provider: BaseProvider | None = None) -> SearchFilters | None:
    """Natural-language search -> SearchFilters, or None if no model/cached answer is available."""
    query = (query or "").strip()[:500]
    if not query:
        return None
    thesis = get_thesis()
    topics = [s.key for t in thesis.themes.values() for s in t.subcategories.values()]
    allowed = (
        f"Allowed themes: {', '.join(thesis.themes)}\nAllowed topics: {', '.join(topics)}\n"
        f"Allowed hiring_role_classes: {', '.join(thesis.hiring_roles)}\nAllowed stages: {', '.join(thesis.stages)}"
    )
    user = f"{allowed}\n\nQuery:\n{wrap_source('Q1', None, 'manual', query, 500)}"
    try:
        res = (provider or get_llm()).structured(
            "search.parse", "fast", SYSTEM_GUARD + "\n\n" + load_prompt("search_parse"), user, _ParsedQuery,
            cache=True, prompt_version=PROMPT_VERSION, max_tokens=1024,
        )
    except LLMError as e:
        log.info("llm_parse_query unavailable: %s", e)
        return None
    p = res.parsed
    entity_types = list(dict.fromkeys(p.entity_types)) or ["company", "person"]
    return SearchFilters(
        entity_types=entity_types,
        themes=[t for t in dict.fromkeys(p.themes) if t in thesis.themes],
        topics=[t for t in dict.fromkeys(p.topics) if t in topics],
        keywords=[k.strip() for k in dict.fromkeys(p.keywords) if k.strip()][:10],
        prior_affiliations=[a.strip() for a in dict.fromkeys(p.prior_affiliations) if a.strip()][:10],
        hiring_role_classes=[r for r in dict.fromkeys(p.hiring_role_classes) if r in thesis.hiring_roles],
        stage=[s for s in dict.fromkeys(p.stage) if s in thesis.stages],
        min_score=_positive(p.min_score) if p.min_score <= 100 else None,
        first_seen_within_days=_positive(p.first_seen_within_days),
        event_within_days=_positive(p.event_within_days),
        accelerating=p.accelerating,
        sort=p.sort,
    )


def llm_refine_topics(
    text: str, candidate_topics: list[str], *, provider: BaseProvider | None = None
) -> list[tuple[str, float]] | None:
    """Score ambiguous thesis-topic candidates for ``text``; None if no model/cached answer is available."""
    candidates = list(dict.fromkeys(candidate_topics))
    if not text.strip() or not candidates:
        return []
    thesis = get_thesis()
    labels = []
    for key in candidates:
        theme_key, _, sub_key = key.partition(".")
        theme = thesis.themes.get(theme_key)
        label = theme.subcategories[sub_key].label if theme and sub_key in theme.subcategories else key
        labels.append(f"- {key}: {label}")
    user = "Candidate topics:\n" + "\n".join(labels) + f"\n\nText:\n{wrap_source('T1', None, 'system', text, 4000)}"
    try:
        res = (provider or get_llm()).structured(
            "topics.refine", "fast", SYSTEM_GUARD + "\n\n" + load_prompt("topics"), user, _TopicOut,
            cache=True, prompt_version=PROMPT_VERSION, max_tokens=1024,
        )
    except LLMError as e:
        log.info("llm_refine_topics unavailable: %s", e)
        return None
    out: dict[str, float] = {}
    for t in res.parsed.topics:
        if t.key in candidates:
            out[t.key] = max(out.get(t.key, 0.0), min(max(float(t.score), 0.0), 1.0))
    return sorted(out.items(), key=lambda kv: kv[1], reverse=True)

"""Plain data passed between the engine stages (no database rows, so every stage is a pure function)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

EVENT_TYPES = ("PRODUCT", "TECHNICAL", "TALENT", "COMMERCIAL", "FINANCING", "COMPETITIVE", "REGULATORY", "RISK",
               "STRATEGIC")
NOISE_TYPE = "NOISE"  # candidate-level label for unmatched raw changes; stored events use materiality NOISE
MATERIALITY = ("MATERIAL", "WATCH", "NOISE")
ACTIONS = ("REVIEW", "MONITOR", "UPDATE_COMPETITIVE_MAP", "RUN_DILIGENCE", "PREPARE_FOUNDER_QUESTIONS", "NO_ACTION")
RELATIONS = ("SELF", "COMPETITOR", "ADJACENT", "INFRASTRUCTURE", "INCUMBENT", "OPEN_SOURCE")
COMP_RELATION = {"DIRECT": "COMPETITOR", "ADJACENT": "ADJACENT", "INFRASTRUCTURE": "INFRASTRUCTURE",
                 "INCUMBENT": "INCUMBENT", "OPEN_SOURCE": "OPEN_SOURCE"}


@dataclass
class SourceRef:
    """One piece of evidence behind an event."""

    url: str | None
    title: str
    source_type: str  # website | usaspending | github | arxiv | hackernews | jobs
    family: str  # web | commercial | github | research | community | hiring
    publisher: str  # company | registry | third_party
    observed_at: datetime | None = None
    ref: str | None = None  # Signal / Source / Job / paper id (string)
    snippet: str | None = None


@dataclass
class RawChange:
    """One raw observation in the review window (a signal, a posting, a closure, a page change, a metric jump)."""

    company_id: uuid.UUID
    kind: str  # signal | paper | job_posted | job_closed | metric_jump
    signal_type: str
    title: str
    event_at: datetime
    source: SourceRef
    description: str | None = None
    confidence: float = 1.0
    epistemic: str = "FACT"
    raw_value: float | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    in_window: bool = True  # False: horizon-only context (novelty), never an event or a funnel count


@dataclass
class Candidate:
    """A typed candidate event (one or more raw changes) before dedup and materiality."""

    company_id: uuid.UUID
    event_type: str  # one of EVENT_TYPES, or NOISE_TYPE
    subtype: str
    title: str
    event_at: datetime
    sources: list[SourceRef]
    epistemic: str = "FACT"
    confidence: float = 1.0
    description: str | None = None
    raw_count: int = 1
    meta: dict[str, Any] = field(default_factory=dict)
    rule: str = ""  # normalisation rule that typed it (for the trace)
    in_window: bool = True

    @property
    def urls(self) -> list[str]:
        return [s.url for s in self.sources if s.url]


@dataclass
class Classified:
    """A deduplicated candidate with its materiality decision."""

    candidate: Candidate
    materiality: str
    rule: str
    reason: str
    novelty: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class CompanyContext:
    """What the materiality rules may consult about the event's subject."""

    company_id: uuid.UUID
    name: str
    domain: str | None = None
    github_org: str | None = None
    is_portfolio: bool = False
    repo_stars: dict[str, int] = field(default_factory=dict)  # owner/name (lower) -> stars
    diligence_claims: list[dict[str, Any]] = field(default_factory=list)  # [{id, text, claim_type, status}]
    tracked_names: list[str] = field(default_factory=list)  # names of tracked companies (counterparty matching)

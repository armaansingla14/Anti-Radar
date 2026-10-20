"""V2 feed shapes: Home (GET /api/home), Daily Radar v2 (GET /api/daily/v2) and brief history
(GET /api/brief/archive, GET /api/brief/day/{date}).

Kept apart from ``schemas/api.py`` (which does not import this module) so the feed contract can grow
without touching the shared schema file. The frontend mirrors these in ``frontend/lib/types.ts``.

Honesty rules carried by every item: ``headline`` / ``why_now`` are deterministic strings computed
from stored rows (never model text); ``evidence`` / ``sources`` point at the stored signals (with
``source_url`` when the source has one); ``confidence`` is evidence *breadth* (independent source
families), not a model judgement.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from radar.schemas.api import (
    EntityRef,
    EvidenceItem,
    FormationCard,
    IntelCard,
    MeetCard,
    Narrative,
    StrangeSignal,
)

Confidence = Literal["HIGH CONFIDENCE", "MEDIUM CONFIDENCE", "EARLY SIGNAL"]
ChangeKind = Literal["formation", "person", "company", "collision", "strange", "research"]
DailyKind = Literal["formation", "person", "company", "collision", "strange", "research", "watch"]
NewWindow = Literal["today", "week"]


# ── Home ─────────────────────────────────────────────────────────────────────


class HomeSummary(BaseModel):
    updated_at: datetime | None = None  # last successful real-provider ingestion (None: never ingested)
    data_as_of: date
    is_snapshot: bool = False
    snapshot_age_days: int | None = None
    possible_formations: int = 0  # formation candidates (any tier) with activity in the last 30 days
    people_to_meet: int = 0  # this week's meet list
    companies_accelerating: int = 0  # like-for-like 7-day ANTI SCORE rise, or measured acceleration
    signal_collisions: int = 0  # entities whose collision strength is HIGH or MEDIUM
    strange_signals: int = 0


class ChangeItem(BaseModel):
    kind: ChangeKind
    headline: str  # deterministic, e.g. "3 people converging on AetherLabs-AI"
    why_now: str  # deterministic, e.g. "+178 GitHub stars in 30 days · first detected Oct 2"
    entity: EntityRef
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: Confidence = "EARLY SIGNAL"
    at: datetime | None = None  # when the change happened (newest backing evidence)
    label: str | None = None  # e.g. POSSIBLE FORMATION · MEET · HIGH COLLISION — display tag
    is_demo: bool = False


class HomeFeed(BaseModel):
    summary: HomeSummary
    what_changed: list[ChangeItem] = Field(default_factory=list)  # <= 8
    formations: list[FormationCard] = Field(default_factory=list)  # <= 3
    people_to_meet: list[MeetCard] = Field(default_factory=list)  # <= 5
    companies: list[IntelCard] = Field(default_factory=list)  # <= 6
    companies_basis: Literal["accelerating", "top_ranked"] = "accelerating"
    companies_note: str | None = None  # set when no company is measurably accelerating
    strange_signals: list[StrangeSignal] = Field(default_factory=list)  # <= 3
    new: NewWindow | None = None  # active "new today / new this week" filter
    demo_mode: bool = False
    snapshot_note: str | None = None


# ── Daily Radar v2 ───────────────────────────────────────────────────────────


class DailyItem(BaseModel):
    kind: DailyKind
    headline: str
    why_now: str
    at: datetime | None = None
    sources: list[EvidenceItem] = Field(default_factory=list)
    confidence: Confidence = "EARLY SIGNAL"
    entity: EntityRef | None = None
    label: str | None = None
    score: int | None = None
    delta: float | None = None
    is_demo: bool = False


class DailyRadarV2(BaseModel):
    date: date
    since: datetime  # same semantics as /api/daily (earlier of now-24h and the previous visit)
    data_as_of: date
    is_snapshot: bool = False
    summary: Narrative | None = None
    what_changed: list[DailyItem] = Field(default_factory=list)
    people: list[DailyItem] = Field(default_factory=list)
    formations: list[DailyItem] = Field(default_factory=list)
    companies_accelerating: list[DailyItem] = Field(default_factory=list)
    research: list[DailyItem] = Field(default_factory=list)
    strange_signals: list[DailyItem] = Field(default_factory=list)
    watchlist_changes: list[DailyItem] = Field(default_factory=list)


# ── Brief history ────────────────────────────────────────────────────────────


class BriefArchiveEntry(BaseModel):
    date: date
    id: str  # ResearchReport id
    title: str
    items: int  # number of "what changed" lines
    headlines: list[str] = Field(default_factory=list)  # first lines of "what changed" (<= 3)
    generator: str = "template"
    generated_at: datetime | None = None
    is_demo: bool = False

"""SINCE LAST SNAPSHOT: what changed between two verified snapshot days (services.changes, GET /api/changes).

"New" always means *new to Radar* (first observed in the later capture), not newly created; creation dates
are given where the source records them. Score changes compare only like with like (live scores of the
same version on both days, entity re-observed in between) and are cohort-relative percentiles.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

from radar.schemas.api import EntityRef

FormationStatus = Literal["NEW", "DROPPED", "STRENGTHENED", "WEAKENED", "UNCHANGED"]


class ChangeItem(BaseModel):
    entity_type: str  # company | person | repository | paper | job | signal
    id: str
    slug: str | None = None
    name: str
    url: str | None = None  # source URL (GitHub, arXiv, job posting, signal source)
    created_at: datetime | None = None  # when the thing itself was created / published / posted, where known
    first_seen_at: datetime | None = None  # when Radar first observed it
    score: float | None = None  # current-day Anti / Founder score when there is one
    detail: str | None = None
    parent: EntityRef | None = None  # the company of a job / repository; the entity a signal is about


class ChangeSection(BaseModel):
    count: int = 0
    items: list[ChangeItem] = Field(default_factory=list)  # top 25, deterministic order


class FamilySignals(BaseModel):
    family: str  # github | research | hiring | web | community | social | other
    label: str
    count: int = 0
    items: list[ChangeItem] = Field(default_factory=list)  # newest 10


class ScoreChange(BaseModel):
    entity_type: str
    id: str
    slug: str | None = None
    name: str
    base_score: float
    current_score: float
    delta: float
    components_delta: dict[str, float] = Field(default_factory=dict)


class ScoreChangeList(BaseModel):
    count: int = 0
    items: list[ScoreChange] = Field(default_factory=list)


class ScoreChanges(BaseModel):
    comparable: bool = False
    reason: str | None = None
    base_version: str | None = None
    current_version: str | None = None
    threshold: float = 5.0  # |delta| at or above this is material
    compared: int = 0  # live on both days, same version, re-observed in between
    unchanged: int = 0  # compared, |delta| below the threshold
    not_reobserved: int = 0  # live on both days but nothing observed since the base day: not compared
    not_comparable: int = 0  # reconstructed or different-version score on one of the days
    no_baseline: int = 0  # scored on the current day only (new to Radar)
    accelerated: ScoreChangeList = Field(default_factory=ScoreChangeList)
    weakened: ScoreChangeList = Field(default_factory=ScoreChangeList)


class CollisionChange(BaseModel):
    entity_type: str
    id: str
    slug: str | None = None
    name: str
    base_strength: str | None = None  # None: no score on the base day
    current_strength: str | None = None
    families: list[str] = Field(default_factory=list)
    new_entity: bool = False


class CollisionChanges(BaseModel):
    count: int = 0
    items: list[CollisionChange] = Field(default_factory=list)


class FormationChange(BaseModel):
    anchor_id: str
    anchor_type: str
    slug: str
    title: str
    kind: str
    status: FormationStatus
    base_score: float | None = None
    current_score: float | None = None
    base_tier: str | None = None
    current_tier: str | None = None
    anchor_slug: str | None = None  # the company/person slug (links work even for DROPPED candidates)
    reason: str | None = None  # DROPPED: why it no longer qualifies


class FormationChanges(BaseModel):
    available: bool = False
    reason: str | None = None
    base_day: date | None = None  # formation-history days actually compared
    current_day: date | None = None
    counts: dict[str, int] = Field(default_factory=dict)  # NEW / DROPPED / STRENGTHENED / WEAKENED / UNCHANGED
    threshold: float = 5.0  # Formation Score points for STRENGTHENED / WEAKENED within a tier
    new: list[FormationChange] = Field(default_factory=list)
    dropped: list[FormationChange] = Field(default_factory=list)
    strengthened: list[FormationChange] = Field(default_factory=list)
    weakened: list[FormationChange] = Field(default_factory=list)


class MeetMove(BaseModel):
    person: EntityRef
    base_rank: int | None = None
    current_rank: int | None = None
    change: int | None = None  # positive: moved up (base_rank - current_rank)


class MeetChanges(BaseModel):
    available: bool = False
    reason: str | None = None
    base_week: str | None = None
    current_week: str | None = None
    entered: list[MeetMove] = Field(default_factory=list)
    left: list[MeetMove] = Field(default_factory=list)
    moved: list[MeetMove] = Field(default_factory=list)  # in both weeks, rank changed


class ChangesReport(BaseModel):
    available: bool = False
    reason: str | None = None
    base_day: date | None = None
    current_day: date | None = None
    points: list[date] = Field(default_factory=list)  # every snapshot day replayed into this database
    is_snapshot: bool = True  # False when the current side is the live database
    new_companies: ChangeSection = Field(default_factory=ChangeSection)
    new_people: ChangeSection = Field(default_factory=ChangeSection)
    new_repositories: ChangeSection = Field(default_factory=ChangeSection)
    new_papers: ChangeSection = Field(default_factory=ChangeSection)
    new_jobs: ChangeSection = Field(default_factory=ChangeSection)  # new hiring
    new_research: ChangeSection = Field(default_factory=ChangeSection)  # new papers by people already on Radar
    new_signals_total: int = 0
    new_signals_by_family: list[FamilySignals] = Field(default_factory=list)
    score_changes: ScoreChanges = Field(default_factory=ScoreChanges)
    momentum_acceleration: ScoreChangeList = Field(default_factory=ScoreChangeList)
    new_collisions: CollisionChanges = Field(default_factory=CollisionChanges)
    collisions_ended: CollisionChanges = Field(default_factory=CollisionChanges)
    formation: FormationChanges = Field(default_factory=FormationChanges)
    meet: MeetChanges = Field(default_factory=MeetChanges)
    caveats: list[str] = Field(default_factory=list)


class SnapshotPoint(BaseModel):
    as_of: date
    kind: str | None = None  # base | update
    link: int | None = None
    files: int | None = None
    captured_at: datetime | None = None


class SnapshotPoints(BaseModel):
    points: list[SnapshotPoint] = Field(default_factory=list)
    latest: date | None = None
    default_base: date | None = None
    default_current: date | None = None
    is_snapshot: bool = True

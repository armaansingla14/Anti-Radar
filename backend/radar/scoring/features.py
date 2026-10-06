"""Feature containers consumed by the (pure) scoring functions.

Convention: ``None`` means *unmeasured / unavailable*; ``0`` means *measured and
zero*. Scoring code must never treat the two the same way.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# Signal types that indicate someone is forming something new.
FORMATION_SIGNAL_TYPES = frozenset(
    {"affiliation_changed", "bio_changed", "formation_language", "x_formation_post"}
)
# Measurement signals restate metric features (stars_30d, commits_4w, new contributors, open roles)
# and are dated when observed, not when the change happened. They are evidence, never collision events:
# counting them would double-count the metrics and make reconstructed history incomparable.
MEASUREMENT_SIGNAL_TYPES = frozenset(
    {"star_velocity", "star_acceleration", "commit_acceleration", "new_contributors", "hiring_active"}
)


@dataclass(frozen=True)
class SignalLite:
    id: uuid.UUID
    signal_type: str
    source_type: str
    title: str
    event_at: datetime
    confidence: float = 1.0
    normalized_value: float | None = None
    source_url: str | None = None
    is_demo: bool = False
    raw_value: float | None = None
    entity_id: uuid.UUID | None = None  # the signal's primary entity (who/what it is about)

    def evidence(self, weight: float | None = None) -> dict[str, Any]:
        """EvidenceItem-shaped dict (schemas/api.py)."""
        out: dict[str, Any] = {
            "signal_id": str(self.id),
            "title": self.title,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "event_at": self.event_at.isoformat(),
        }
        if weight is not None:
            out["weight"] = round(weight, 4)
        return out


@dataclass
class EntityFeatures:
    entity_id: uuid.UUID
    name: str
    first_seen_at: datetime | None = None
    entity_type: str = "company"
    stage: str = "unknown"
    kind: str = "company"
    stars_total: float | None = None
    stars_7d: float | None = None
    stars_30d: float | None = None
    stars_prev_28d: float | None = None  # stars gained between 35 and 7 days ago
    forks_30d: float | None = None
    commits_4w: float | None = None
    commits_prev_4w: float | None = None
    new_contributors_30d: float | None = None
    contributors_total: float | None = None
    releases_90d: float | None = None
    open_roles: int | None = None
    technical_roles_30d: int | None = None
    hn_points_90d: float | None = None
    hn_mentions_90d: int | None = None
    x_followers: float | None = None
    press_mentions: int | None = None
    topic_scores: dict[str, float] = field(default_factory=dict)
    theme_scores: dict[str, float] = field(default_factory=dict)
    topic_evidence: dict[str, list[str]] = field(default_factory=dict)  # topic key -> matched keywords
    founder_scores: list[float] = field(default_factory=list)
    founder_ids: list[uuid.UUID] = field(default_factory=list)  # CompanyPerson.is_founder links only
    linked_people: int = 0  # all current CompanyPerson links (contributors included)
    signals: list[SignalLite] = field(default_factory=list)
    tokens: set[str] = field(default_factory=set)
    is_demo: bool = False
    has_history: bool = False


@dataclass
class PersonFeatures:
    person_id: uuid.UUID
    name: str
    first_seen_at: datetime | None = None
    followers: float | None = None
    owned_repo_stars: float | None = None  # stars of key repos (owned or core-contributor), share-weighted
    key_repos: list[str] = field(default_factory=list)
    recent_repo_stars_30d: float | None = None
    commits_30d: float | None = None  # proxy, see extract.build_person_features
    papers_24mo: int = 0
    papers_with_code: int = 0
    affiliations: list[str] = field(default_factory=list)
    tier: float = 0.0
    tier_match: str | None = None
    recent_transition: bool = False
    transition_evidence: list[uuid.UUID] = field(default_factory=list)
    joined_young_org: bool = False
    young_org_names: list[str] = field(default_factory=list)
    topic_scores: dict[str, float] = field(default_factory=dict)
    theme_scores: dict[str, float] = field(default_factory=dict)
    topic_evidence: dict[str, list[str]] = field(default_factory=dict)
    signals: list[SignalLite] = field(default_factory=list)  # person + key-repo signals (evidence)
    # Signals about this person (primary entity, or linked to the person directly), without other
    # people's formation signals. Formation is scored only from these.
    own_signals: list[SignalLite] = field(default_factory=list)
    is_demo: bool = False


@dataclass
class Component:
    """One explainable score component (maps to schemas/api.py ScoreComponent)."""

    key: str
    label: str
    value: float
    max: float
    explanation: str
    raw: dict[str, Any] = field(default_factory=dict)
    evidence: list[tuple[SignalLite, float | None]] = field(default_factory=list)

    def to_api(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "value": round(self.value, 2),
            "max": self.max,
            "explanation": self.explanation,
            "raw": self.raw,
            "evidence": [s.evidence(w) for s, w in self.evidence],
        }


def top_signals(
    signals: list[SignalLite], predicate, limit: int = 5
) -> list[tuple[SignalLite, float | None]]:
    """Strongest, most recent signals matching ``predicate`` as evidence pairs."""
    picked = [s for s in signals if predicate(s)]
    picked.sort(
        key=lambda s: (
            s.confidence * (s.normalized_value if s.normalized_value is not None else 0.5),
            s.event_at,
        ),
        reverse=True,
    )
    return [(s, None) for s in picked[:limit]]

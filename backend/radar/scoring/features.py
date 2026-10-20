"""Feature containers consumed by the (pure) scoring functions.

Convention: ``None`` means *unmeasured / unavailable*; ``0`` means *measured and
zero*. Scoring code must never treat the two the same way.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

# A person's own statements/profile changes that suggest a transition (affiliation/bio change, formation language).
TRANSITION_SIGNAL_TYPES = frozenset(
    {"affiliation_changed", "bio_changed", "formation_language", "x_formation_post"}
)
# Discrete, dated formation *events* about a person (dated at repo/org creation or first commit — never
# observation time, so they are not measurement signals): a new non-fork repository of their own in a thesis
# area, or becoming a contributor/public member of a young organization. A repository is not a company.
FORMATION_EVENT_TYPES = frozenset({"new_project", "joined_young_org"})
# Signal types that indicate someone is forming something new.
FORMATION_SIGNAL_TYPES = TRANSITION_SIGNAL_TYPES | FORMATION_EVENT_TYPES
# Several talent-pool people (verified lab contributors) committing to the same new org/repository. The signal's
# primary entity is the org/repo; members are linked through signal_entities.
LAB_ALUMNI_CLUSTER = "lab_alumni_cluster"
# Measurement signals restate metric features (stars_30d, commits_4w, new contributors, open roles)
# and are dated when observed, not when the change happened. They are evidence, never collision events:
# counting them would double-count the metrics and make reconstructed history incomparable.
MEASUREMENT_SIGNAL_TYPES = frozenset(
    {"star_velocity", "star_acceleration", "commit_acceleration", "new_contributors", "hiring_active",
     "young_org_team"}
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
    # Signal metadata, loaded only for formation events / lab-alumni clusters (see extract.load_signal_index).
    meta: dict[str, Any] | None = field(default=None, compare=False, hash=False)

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


# ── Talent pool: verified lab / frontier-OSS contributions ─────────────────────
# ``Person.attrs['lab_contributions']`` = [{org, label, tier, repo, commits, first_commit_at, last_commit_at}],
# written from the GitHub API (commits on the default branch). "Contributed N commits to org/repo" is a VERIFIED
# fact; it never implies employment ("worked at") — employment stays self-reported/INFERRED.

LAB_TIER_WEIGHT = {1: 1.0, 2: 0.6}
LAB_MIN_COMMITS = 3  # fewer commits (a typo fix) is not technical history
LAB_FULL_COMMITS = 100  # depth reaches 1.0 at this many commits (log scale)
LAB_DEPTH_FLOOR = 0.35
# Self-reported / listed affiliation strings count for less than verified history (contributions, public org
# membership observed via the GitHub API).
SELF_REPORTED_FACTOR = 0.7


def _as_dt(v: Any) -> datetime | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=UTC)
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day, tzinfo=UTC)
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


@dataclass(frozen=True)
class LabContribution:
    """One verified contribution history to a lab / frontier-OSS repository."""

    org: str
    label: str
    tier: int
    repo: str  # repository name (without the owner)
    commits: int
    first_commit_at: datetime | None = None
    last_commit_at: datetime | None = None

    @property
    def full_name(self) -> str:
        return f"{self.org}/{self.repo}"

    @property
    def tier_label(self) -> str:
        return f"tier-{self.tier}"

    @property
    def depth(self) -> float:
        """0.35..1 by commits on a log scale (3 commits ~0.55, 20 ~0.78, >= 100 -> 1)."""
        c = max(self.commits, 0)
        return LAB_DEPTH_FLOOR + (1 - LAB_DEPTH_FLOOR) * min(1.0, math.log1p(c) / math.log1p(LAB_FULL_COMMITS))

    @property
    def strength(self) -> float:
        return LAB_TIER_WEIGHT.get(self.tier, 0.0) * self.depth

    def years(self) -> str | None:
        """'2024–2025' / '2025' from first/last commit dates (None if unknown)."""
        ys = sorted({d.year for d in (self.first_commit_at, self.last_commit_at) if d is not None})
        if not ys:
            return None
        return str(ys[0]) if len(ys) == 1 else f"{ys[0]}\u2013{ys[-1]}"

    def phrase(self, *, verb: bool = True) -> str:
        """'Contributed 412 commits to google-deepmind/mujoco (tier-1 lab repository, verified)'."""
        n = f"{self.commits:,} commit{'s' if self.commits != 1 else ''}"
        head = f"Contributed {n} to" if verb else f"{n} to"
        return f"{head} {self.full_name} ({self.tier_label} lab repository, verified)"

    def to_json(self) -> dict[str, Any]:
        return {"org": self.org, "label": self.label, "tier": self.tier, "repo": self.repo,
                "full_name": self.full_name, "commits": self.commits,
                "first_commit_at": self.first_commit_at.isoformat() if self.first_commit_at else None,
                "last_commit_at": self.last_commit_at.isoformat() if self.last_commit_at else None}


def parse_lab_contributions(
    items: Iterable[Any] | None, tier_of: Callable[[str], int | None] | Mapping[str, int] | None = None,
) -> list[LabContribution]:
    """Validated lab contributions (>= LAB_MIN_COMMITS, known tier), strongest first, one per repository.

    ``tier_of`` (org login -> 1|2) fills a missing tier (config/talent_sources.yaml)."""
    best: dict[str, LabContribution] = {}
    for it in items or []:
        if not isinstance(it, Mapping):
            continue
        org, repo = str(it.get("org") or "").strip(), str(it.get("repo") or "").strip()
        if "/" in repo:
            owner, repo = repo.split("/", 1)
            org = org or owner
        if not org or not repo:
            continue
        try:
            commits = int(it.get("commits") or 0)
        except (TypeError, ValueError):
            continue
        if commits < LAB_MIN_COMMITS:
            continue
        tier = it.get("tier")
        if tier is None and tier_of is not None:
            tier = tier_of.get(org.lower()) if isinstance(tier_of, Mapping) else tier_of(org)
        try:
            tier = int(tier) if tier is not None else None
        except (TypeError, ValueError):
            tier = None
        if tier not in LAB_TIER_WEIGHT:
            continue
        lc = LabContribution(org=org, label=str(it.get("label") or org), tier=tier, repo=repo, commits=commits,
                             first_commit_at=_as_dt(it.get("first_commit_at")),
                             last_commit_at=_as_dt(it.get("last_commit_at")))
        key = lc.full_name.lower()
        if key not in best or lc.commits > best[key].commits:
            best[key] = lc
    return sorted(best.values(), key=lambda c: (-c.strength, -c.commits, c.full_name.lower()))


def lab_strength(labs: Iterable[LabContribution]) -> float:
    """0..1: the strongest verified lab contribution (tier weight x commit depth)."""
    return max((c.strength for c in labs), default=0.0)


def pedigree_strength(tier: float, basis: str | None, labs: Iterable[LabContribution],
                      self_reported: bool = True) -> tuple[float, str]:
    """(0..1, source) — the best of verified lab contributions, a public GitHub org membership (observed), and
    an affiliation string — discounted by ``SELF_REPORTED_FACTOR`` when it is self-reported (the person's own
    profile; a curated/listed affiliation keeps its tier). Source: 'lab_contribution' | 'public_github_org' |
    'affiliation' | 'none'."""
    lab = lab_strength(labs)
    discount = SELF_REPORTED_FACTOR if self_reported and basis != "public_github_org" else 1.0
    other = tier * discount
    if lab <= 0 and other <= 0:
        return 0.0, "none"
    if lab >= other:
        return min(lab, 1.0), "lab_contribution"
    return min(other, 1.0), ("public_github_org" if basis == "public_github_org" else "affiliation")


def lab_summary(labs: list[LabContribution], limit: int = 2) -> str:
    """'412 commits to google-deepmind/mujoco; 37 commits to NVIDIA/cutlass' (verified facts, no employment)."""
    return "; ".join(c.phrase(verb=False).split(" (")[0] for c in labs[:limit])


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
    # Where the affiliation tier came from (honest explanations, founder.pedigree).
    tier_basis: str | None = None
    tier_org_login: str | None = None
    public_orgs: list[str] = field(default_factory=list)
    affiliation_self_reported: bool = False  # affiliation strings come from the person's own profile
    # Verified talent-pool history (Person.attrs['lab_contributions']), strongest first.
    lab_contributions: list[LabContribution] = field(default_factory=list)
    # Own dated formation events (FORMATION_EVENT_TYPES) and lab-alumni clusters the person is part of.
    new_projects: list[str] = field(default_factory=list)  # repo full names
    lab_clusters: list[str] = field(default_factory=list)  # cluster signal titles


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

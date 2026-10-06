"""X (Twitter) provider family: shared models, protocol and post → signal conversion.

Access is legitimate-only: the official X API v2 (bearer token), the public oEmbed
endpoint for individual posts a user pastes, or clearly-labelled demo fixtures.
Nothing here scrapes x.com or fabricates posts.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from radar import clock
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    MetricPoint,
    NormalizedSignal,
    PersonRecord,
    ProviderBatch,
)
from radar.providers.formation import GENERIC_PHRASES, detect_formation_phrases
from radar.providers.http import content_hash
from radar.settings import Settings, get_settings
from radar.thesis.loader import Thesis, get_thesis

HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
LAUNCH_RE = re.compile(r"\b(?:launch(?:ing|ed)?|introduc(?:ing|ed)|announc(?:ing|ed)|releas(?:ing|ed)|unveil(?:ing|ed))\b",
                       re.IGNORECASE)
HIRING_RE = re.compile(
    r"\b(?:hiring|we(?:'|’)re looking for|we are looking for|join (?:our|the) team|open roles?|job openings?)\b", re.IGNORECASE
)
SIGNAL_STRENGTH = {"x_formation_post": 0.7, "x_launch_post": 0.5, "x_hiring_post": 0.4, "x_post": 0.1}
TWITTER_EPOCH_MS = 1288834974657  # ms; snowflake IDs encode creation time relative to this
FIRST_SNOWFLAKE_ID = 29_700_859_247


class XUser(BaseModel):
    username: str
    id: str | None = None
    name: str | None = None
    description: str | None = None
    location: str | None = None
    url: str | None = None
    created_at: datetime | None = None
    followers_count: int | None = None
    following_count: int | None = None
    tweet_count: int | None = None

    @property
    def profile_url(self) -> str:
        return f"https://x.com/{self.username}"


class XPost(BaseModel):
    id: str
    author_username: str
    text: str
    created_at: datetime
    url: str | None = None  # None only for synthetic demo posts
    public_metrics: dict[str, int] = Field(default_factory=dict)


class XProvider(Protocol):
    name: str

    def enabled(self) -> tuple[bool, str]: ...

    def get_user(self, handle: str) -> XUser | None: ...

    def recent_posts(self, handle: str, limit: int = 20) -> list[XPost]: ...

    def search_recent(self, query: str, limit: int = 20) -> list[XPost]: ...


def valid_handle(handle: str) -> bool:
    return bool(HANDLE_RE.match(handle or ""))


def post_url(handle: str, post_id: str) -> str:
    return f"https://x.com/{handle}/status/{post_id}"


def snowflake_time(post_id: str) -> datetime | None:
    """Creation time encoded in a post's snowflake ID (exact to the millisecond)."""
    if not post_id.isdigit() or int(post_id) < FIRST_SNOWFLAKE_ID:
        return None
    ms = (int(post_id) >> 22) + TWITTER_EPOCH_MS
    return datetime.fromtimestamp(ms / 1000, tz=UTC)


def x_person_ref(handle: str) -> EntityRef:
    return EntityRef(type="person", key=f"x:{handle.lower()}")


def classify_post(text: str, thesis: Thesis) -> tuple[str, list[str]]:
    """(signal_type, matched formation phrases).

    Priority: formation > launch > hiring > generic. "launching" / "we're hiring" alone are
    treated as launch / hiring posts, not formation.
    """
    phrases = detect_formation_phrases(text, thesis)
    if any(p not in GENERIC_PHRASES for p in phrases):
        return "x_formation_post", phrases
    if LAUNCH_RE.search(text):
        return "x_launch_post", phrases
    if HIRING_RE.search(text) or "we're hiring" in phrases:
        return "x_hiring_post", phrases
    return "x_post", phrases


def _subject_record(user: XUser, entity_ref: EntityRef) -> PersonRecord | CompanyRecord | None:
    """Record for the posting account when the subject entity *is* that account."""
    if entity_ref.key != f"x:{user.username.lower()}":
        return None
    name = user.name or f"@{user.username}"
    if entity_ref.type == "person":
        return PersonRecord(ref=entity_ref, name=name, bio=user.description, location=user.location,
                            x_handle=user.username, personal_site=user.url, followers=user.followers_count,
                            source_url=user.profile_url)
    if entity_ref.type == "company":
        return CompanyRecord(ref=entity_ref, name=name, description=user.description, x_handle=user.username,
                             location=user.location, source_url=user.profile_url)
    return None


def posts_to_batch(
    posts: list[XPost],
    user: XUser | None,
    entity_ref: EntityRef,
    source_label: str,
    *,
    thesis: Thesis | None = None,
    confidence: float = 1.0,
    extra_metadata: dict[str, Any] | None = None,
    metric_source: Literal["observed", "backfill", "demo"] = "observed",
) -> ProviderBatch:
    """Convert posts into FACT signals about `entity_ref` (source_type 'x').

    `source_label` names the access path (e.g. 'x_api', 'manual', 'demo') and is recorded in
    metadata.source; `extra_metadata` is merged into every signal's metadata.
    """
    thesis = thesis or get_thesis()
    batch = ProviderBatch(provider=source_label)
    if user is not None:
        rec = _subject_record(user, entity_ref)
        if isinstance(rec, PersonRecord):
            batch.people.append(rec)
        elif isinstance(rec, CompanyRecord):
            batch.companies.append(rec)
        if user.followers_count is not None:
            batch.metrics.append(MetricPoint(entity=entity_ref, metric="followers", day=clock.today(),
                                             value=float(user.followers_count), source=metric_source))
    for post in posts:
        signal_type, phrases = classify_post(post.text, thesis)
        text = re.sub(r"\s+", " ", post.text).strip()
        metadata: dict[str, Any] = {
            "source": source_label,
            "post_id": post.id,
            "handle": post.author_username,
            "matched_phrases": phrases,
            "public_metrics": post.public_metrics,
            **(extra_metadata or {}),
        }
        batch.signals.append(NormalizedSignal(
            entity=entity_ref,
            signal_type=signal_type,
            source_type="x",
            source_url=post.url,
            title=f"@{post.author_username}: {text[:110]}{'…' if len(text) > 110 else ''}",
            description=text[:2000],
            normalized_value=SIGNAL_STRENGTH[signal_type],
            event_at=post.created_at,
            confidence=confidence,
            epistemic="FACT",
            metadata=metadata,
        ))
        if post.url:
            batch.sources.append({"url": post.url, "source_type": "x", "title": f"@{post.author_username} on X",
                                  "snippet": text[:300], "content_hash": content_hash(text)})
    return batch


class DisabledXProvider:
    name = "x"

    def __init__(self, reason: str = "X_BEARER_TOKEN required — official X API v2 access") -> None:
        self.reason = reason

    def enabled(self) -> tuple[bool, str]:
        return False, self.reason

    def get_user(self, handle: str) -> XUser | None:
        return None

    def recent_posts(self, handle: str, limit: int = 20) -> list[XPost]:
        return []

    def search_recent(self, query: str, limit: int = 20) -> list[XPost]:
        return []


def get_x_provider(settings: Settings | None = None) -> XProvider:
    """Official API provider when X_BEARER_TOKEN is configured, else a disabled provider."""
    settings = settings or get_settings()
    if settings.x_bearer_token:
        from radar.providers.x.api import XApiProvider

        return XApiProvider(settings.x_bearer_token)
    return DisabledXProvider()

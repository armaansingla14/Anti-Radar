"""Demo-only X provider: replays fictional posts from demo fixtures, flagged synthetic.

It refuses any handle that is not a demo fixture, so it can never put words in a real
person's mouth.
"""

from __future__ import annotations

from typing import Any

from dateutil import parser as dateparser

from radar.providers.base import EntityRef, ProviderBatch
from radar.providers.x.base import XPost, XUser, posts_to_batch, valid_handle, x_person_ref
from radar.thesis.loader import Thesis


class NotADemoHandle(PermissionError):
    pass


class MockXProvider:
    """Fixtures: [{"handle", "is_demo": true, "name"?, "description"?, "followers_count"?,
    "posts": [{"id", "text", "created_at", "url"?}]}]. Non-demo fixtures are rejected."""

    name = "x_demo"

    def __init__(self, fixtures: list[dict[str, Any]]) -> None:
        self._users: dict[str, XUser] = {}
        self._posts: dict[str, list[XPost]] = {}
        for fx in fixtures:
            handle = str(fx.get("handle", ""))
            if fx.get("is_demo") is not True:
                raise NotADemoHandle(f"fixture for {handle!r} is not marked is_demo; refusing")
            if not valid_handle(handle):
                raise ValueError(f"invalid demo handle {handle!r}")
            key = handle.lower()
            self._users[key] = XUser(username=handle, name=fx.get("name"), description=fx.get("description"),
                                     followers_count=fx.get("followers_count"))
            self._posts[key] = sorted(
                (XPost(id=str(p["id"]), author_username=handle, text=p["text"],
                       created_at=dateparser.isoparse(p["created_at"]), url=p.get("url"))
                 for p in fx.get("posts") or []),
                key=lambda p: p.created_at,
                reverse=True,
            )

    def enabled(self) -> tuple[bool, str]:
        return True, "demo fixtures only"

    def _require_demo(self, handle: str) -> str:
        key = (handle or "").lower()
        if key not in self._users:
            raise NotADemoHandle(f"@{handle} is not a demo entity; the mock X provider serves demo fixtures only")
        return key

    def get_user(self, handle: str) -> XUser | None:
        return self._users[self._require_demo(handle)]

    def recent_posts(self, handle: str, limit: int = 20) -> list[XPost]:
        return self._posts[self._require_demo(handle)][:limit]

    def search_recent(self, query: str, limit: int = 20) -> list[XPost]:
        terms = [t.lower() for t in query.split() if t]
        hits = [p for posts in self._posts.values() for p in posts if all(t in p.text.lower() for t in terms)]
        return sorted(hits, key=lambda p: p.created_at, reverse=True)[:limit]

    def to_batch(self, handle: str, *, entity_ref: EntityRef | None = None, thesis: Thesis | None = None,
                 limit: int = 20) -> ProviderBatch:
        """Signals for a demo handle; every signal carries metadata.synthetic=True."""
        user = self.get_user(handle)
        assert user is not None
        batch = posts_to_batch(self.recent_posts(handle, limit), user, entity_ref or x_person_ref(user.username),
                               "demo", thesis=thesis, extra_metadata={"synthetic": True}, metric_source="demo")
        batch.provider = self.name
        return batch

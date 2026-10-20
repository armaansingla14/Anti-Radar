"""Official X API v2 provider (bearer-token, read-only endpoints).

Rate-limit aware: every response's ``x-rate-limit-remaining`` / ``x-rate-limit-reset`` headers are
recorded; once the remaining quota hits 0 (or the API answers 429) the provider makes no further
calls this run and reports when the window resets. Stopping on a limit is not an error.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dateutil import parser as dateparser

from radar.providers.ats import IDENTITY_ENCODING
from radar.providers.http import HttpClient
from radar.providers.x.base import XPost, XUser, post_url, valid_handle

API = "https://api.x.com/2"
USER_FIELDS = "description,public_metrics,created_at,url,location"
SEARCH_USER_FIELDS = "username,name,description,public_metrics"  # only what a person record needs
TWEET_FIELDS = "created_at,public_metrics,author_id"


class OfficialXProvider:
    """X API v2 client. On HTTP 429 or an exhausted quota it records the limit and makes no further calls."""

    name = "x"

    def __init__(self, bearer_token: str, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(min_interval=1.0, max_retries=0, headers=IDENTITY_ENCODING)
        self._auth = {"Authorization": f"Bearer {bearer_token}"}
        self.rate_limited_until: datetime | None = None
        self.rate_limited = False
        self.last_error: str | None = None
        self.rate_limit_remaining: int | None = None
        self.requests = 0
        self._users: dict[str, XUser] = {}

    def enabled(self) -> tuple[bool, str]:
        if self.rate_limited:
            until = self.rate_limited_until.isoformat() if self.rate_limited_until else "reset"
            return False, f"X API rate limit reached; paused until {until}"
        return True, "official X API v2"

    def _note_reset(self, headers: Any) -> None:
        reset = headers.get("x-rate-limit-reset")
        if reset and reset.isdigit():
            self.rate_limited_until = datetime.fromtimestamp(int(reset), tz=UTC)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if self.rate_limited:
            return None
        resp = self.client.get(f"{API}{path}", params=params, headers=self._auth)
        self.requests += 1
        remaining = resp.headers.get("x-rate-limit-remaining")
        if remaining and remaining.isdigit():
            self.rate_limit_remaining = int(remaining)
            self.client.stats.rate_limit_remaining = int(remaining)
        if resp.status_code == 429:
            # A handled condition (clean stop), not a failed request.
            self.client.stats.errors = max(0, self.client.stats.errors - 1)
            self.rate_limited = True
            self._note_reset(resp.headers)
            self.last_error = "rate limited (429)"
            return None
        if self.rate_limit_remaining == 0:
            # Quota exhausted by this call: keep its data, make no further calls this run.
            self.rate_limited = True
            self._note_reset(resp.headers)
        if resp.status_code >= 400:
            self.last_error = f"HTTP {resp.status_code} for {path}"
            return None
        body = resp.json()
        if "data" not in body and body.get("errors"):
            self.last_error = str(body["errors"][0].get("detail") or body["errors"][0])[:200]
        return body

    @staticmethod
    def _user(d: dict[str, Any]) -> XUser:
        pm = d.get("public_metrics") or {}
        return XUser(
            id=d.get("id"),
            username=d["username"],
            name=d.get("name"),
            description=d.get("description") or None,
            location=d.get("location"),
            url=d.get("url") or None,
            created_at=dateparser.isoparse(d["created_at"]) if d.get("created_at") else None,
            followers_count=pm.get("followers_count"),
            following_count=pm.get("following_count"),
            tweet_count=pm.get("tweet_count"),
        )

    @staticmethod
    def _post(d: dict[str, Any], username: str) -> XPost | None:
        if not d.get("id") or not d.get("created_at"):
            return None
        return XPost(
            id=str(d["id"]),
            author_username=username,
            text=d.get("text") or "",
            created_at=dateparser.isoparse(d["created_at"]),
            url=post_url(username, str(d["id"])),
            public_metrics=d.get("public_metrics") or {},
        )

    def get_user(self, handle: str) -> XUser | None:
        if not valid_handle(handle):
            return None
        key = handle.lower()
        if key not in self._users:
            body = self._get(f"/users/by/username/{handle}", {"user.fields": USER_FIELDS})
            if not body or not body.get("data"):
                return None
            self._users[key] = self._user(body["data"])
        return self._users[key]

    def recent_posts(self, handle: str, limit: int = 20) -> list[XPost]:
        user = self.get_user(handle)
        if not user or not user.id:
            return []
        body = self._get(
            f"/users/{user.id}/tweets",
            {"max_results": max(5, min(100, limit)), "tweet.fields": TWEET_FIELDS},
        )
        posts = [self._post(d, user.username) for d in (body or {}).get("data") or []]
        return [p for p in posts if p][:limit]

    def search_recent(self, query: str, limit: int = 20) -> list[XPost]:
        return self.search_recent_with_authors(query, limit)[0]

    def search_recent_with_authors(
        self, query: str, limit: int = 20, *, start_time: datetime | None = None
    ) -> tuple[list[XPost], dict[str, XUser]]:
        """Recent search → (posts, author username (lowercase) → XUser). Posts whose author is not in
        the response's user expansion are dropped (never guess an author)."""
        params: dict[str, Any] = {
            "query": query,
            "max_results": max(10, min(100, limit)),
            "tweet.fields": TWEET_FIELDS,
            "expansions": "author_id",
            "user.fields": SEARCH_USER_FIELDS,
        }
        if start_time is not None:
            params["start_time"] = start_time.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        body = self._get("/tweets/search/recent", params)
        if not body:
            return [], {}
        users_by_id: dict[str, XUser] = {}
        for u in (body.get("includes") or {}).get("users") or []:
            if u.get("id") and valid_handle(str(u.get("username") or "")):
                users_by_id[str(u["id"])] = self._user(u)
        out: list[XPost] = []
        for d in body.get("data") or []:
            user = users_by_id.get(str(d.get("author_id")))
            post = self._post(d, user.username) if user else None  # never guess an author
            if post:
                out.append(post)
        authors = {u.username.lower(): u for u in users_by_id.values()}
        return out[:limit], authors


# Backwards-compatible name (V1).
XApiProvider = OfficialXProvider

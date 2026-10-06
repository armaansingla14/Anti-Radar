"""Official X API v2 provider (bearer-token, read-only endpoints)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from dateutil import parser as dateparser

from radar.providers.ats import IDENTITY_ENCODING
from radar.providers.http import HttpClient
from radar.providers.x.base import XPost, XUser, post_url, valid_handle

API = "https://api.x.com/2"
USER_FIELDS = "description,public_metrics,created_at,url,location"
TWEET_FIELDS = "created_at,public_metrics,author_id"


class XApiProvider:
    """X API v2 client. On HTTP 429 it records the limit and makes no further calls this run."""

    name = "x"

    def __init__(self, bearer_token: str, client: HttpClient | None = None) -> None:
        self.client = client or HttpClient(min_interval=1.0, max_retries=0, headers=IDENTITY_ENCODING)
        self._auth = {"Authorization": f"Bearer {bearer_token}"}
        self.rate_limited_until: datetime | None = None
        self.rate_limited = False
        self.last_error: str | None = None
        self._users: dict[str, XUser] = {}

    def enabled(self) -> tuple[bool, str]:
        if self.rate_limited:
            until = self.rate_limited_until.isoformat() if self.rate_limited_until else "reset"
            return False, f"X API rate limit reached; paused until {until}"
        return True, "official X API v2"

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if self.rate_limited:
            return None
        resp = self.client.get(f"{API}{path}", params=params, headers=self._auth)
        if resp.status_code == 429:
            self.rate_limited = True
            reset = resp.headers.get("x-rate-limit-reset")
            if reset and reset.isdigit():
                self.rate_limited_until = datetime.fromtimestamp(int(reset), tz=UTC)
            self.last_error = "rate limited (429)"
            return None
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
        body = self._get(
            "/tweets/search/recent",
            {
                "query": query,
                "max_results": max(10, min(100, limit)),
                "tweet.fields": TWEET_FIELDS,
                "expansions": "author_id",
                "user.fields": "username",
            },
        )
        if not body:
            return []
        authors = {u["id"]: u["username"] for u in (body.get("includes") or {}).get("users") or []}
        out: list[XPost] = []
        for d in body.get("data") or []:
            username = authors.get(d.get("author_id"))
            post = self._post(d, username) if username else None  # never guess an author
            if post:
                out.append(post)
        return out[:limit]

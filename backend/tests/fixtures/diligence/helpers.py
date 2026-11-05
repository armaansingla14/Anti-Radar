"""Test helpers for diligence: artifact builders and an offline stub HTTP client (no network, ever)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from radar.diligence.types import ArtifactDraft, ClaimDraft, CompanyFacts

HERE = Path(__file__).resolve().parent
SAMPLE_CAPTURE = HERE / "sample_capture.json.gz"
AS_OF = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
REPO = "acme-fixture/engine"


def art(key: str, kind: str, publisher: str, text: str = "", data: dict[str, Any] | None = None,
        url: str | None = None, title: str | None = None) -> ArtifactDraft:
    return ArtifactDraft(key=key, kind=kind, publisher=publisher, url=url or f"https://example.org/{key}",
                         title=title or key, fetched_at=AS_OF, http_status=200, text=text, data=data or {})


def page(text: str, kind: str = "home", url: str = "https://acme.example/") -> ArtifactDraft:
    return art(f"web:{url}", "web_page", "company", text, {"page_kind": kind}, url=url)


def readme(text: str, repo: str = REPO) -> ArtifactDraft:
    return art(f"readme:{repo}", "readme", "company", text, {"full_name": repo})


def repo(stars: int = 100, license_spdx: str | None = "Apache-2.0", pushed_days: int = 3, archived: bool = False,
         name: str = REPO, created: str = "2024-01-01T00:00:00+00:00") -> ArtifactDraft:
    from datetime import timedelta

    return art(f"repo:{name}", "repo", "code", "", {
        "full_name": name, "license_spdx": license_spdx, "archived": archived, "stars": stars, "forks": 10,
        "pushed_at": (AS_OF - timedelta(days=pushed_days)).isoformat(), "created_at": created,
    }, url=f"https://github.com/{name}")


def tree(paths: list[str], name: str = REPO) -> ArtifactDraft:
    return art(f"tree:{name}", "tree", "code", "", {"full_name": name, "paths": sorted(paths)},
               url=f"https://github.com/{name}/tree/main")


def gh_file(path: str, text: str, name: str = REPO) -> ArtifactDraft:
    return art(f"file:{name}:{path}", "file", "code", text, {"path": path, "full_name": name},
               url=f"https://github.com/{name}/blob/main/{path}")


def releases(items: list[dict[str, Any]], name: str = REPO) -> ArtifactDraft:
    return art(f"releases:{name}", "releases", "code", "", {"full_name": name, "releases": items})


def package(name: str, roles: list[str], latest: str | None, latest_at: str | None = None,
            versions: list[dict[str, Any]] | None = None, **extra: Any) -> ArtifactDraft:
    return art(f"pkg:pypi:{name}", "package", "registry", "", {
        "ecosystem": "pypi", "name": name, "roles": roles, "latest_version": latest, "latest_release_at": latest_at,
        "versions": versions or [], "deprecated": None, **extra}, url=f"https://pypi.org/project/{name}/")


def facts(name: str = "Acme", **kw: Any) -> CompanyFacts:
    return CompanyFacts(name=name, slug=name.lower(), **kw)


def claim(text: str, ctype: str = "TECHNICAL_CAPABILITY", ordinal: int = 1, source_key: str = "web:x") -> ClaimDraft:
    return ClaimDraft(text=text, claim_type=ctype, source_key=source_key, source_url="https://acme.example/",
                      source_quote=text, ordinal=ordinal)


def ev(publisher: str, relation: str, strength: str = "direct", verifies: bool = False, key: str = "a",
       gap: bool = False) -> dict[str, Any]:
    return {"artifact_key": key, "url": "", "title": key, "publisher": publisher, "kind": "x", "relation": relation,
            "strength": strength, "verifies": verifies, "quote": "", "observed_at": None, "matcher": "test",
            "note": f"{publisher} {relation}", "gap": gap}


class StubClient:
    """Answers GETs from a route table; anything else is a 404. Records every URL it was asked for."""

    def __init__(self, routes: dict[str, Any] | None = None, post: dict[str, Any] | None = None) -> None:
        self.routes = routes or {}
        self.post_routes = post or {}
        self.calls: list[str] = []

    @staticmethod
    def _key(url: str, params: dict[str, Any] | None) -> str:
        return url + ("?" + urlencode(sorted((k, str(v)) for k, v in params.items())) if params else "")

    def get(self, url: str, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None,
            max_bytes: int | None = None) -> httpx.Response:
        key = self._key(url, params)
        self.calls.append(key)
        hit = self.routes.get(key, self.routes.get(url))
        if hit is None:
            return httpx.Response(404, content=b"not found", request=httpx.Request("GET", url))
        status, body, ctype = hit if isinstance(hit, tuple) else (200, hit, None)
        if isinstance(body, (dict, list)):
            content, ctype = json.dumps(body).encode(), ctype or "application/json"
        else:
            content = body.encode() if isinstance(body, str) else body
        return httpx.Response(status, content=content, headers={"content-type": ctype or "text/html; charset=utf-8"},
                              request=httpx.Request("GET", url))

    def post_json(self, url: str, payload: dict[str, Any]) -> tuple[int, Any, int]:
        self.calls.append(f"POST {url}")
        data = self.post_routes.get(url)
        return (200, data, len(json.dumps(data))) if data is not None else (404, None, 0)


def stub_fetcher(routes: dict[str, Any] | None = None, *, post: dict[str, Any] | None = None, budget_s: float = 60,
                 token: str | None = None):
    """A Fetcher whose every client is the same StubClient; DNS checks are skipped (IP literals still checked)."""
    from radar.diligence.collect import Fetcher

    stub = StubClient(routes, post)
    f = Fetcher(budget_s=budget_s, github_token=token,
                clients={n: stub for n in ("web", "github", "api", "hn", "arxiv")}, resolve=lambda url: None)
    return f, stub

"""GitHub provider: repositories, orgs, contributors and *real* history.

Everything here is derived from the GitHub REST API (v3). Time series are
reconstructed from primary data, never invented:

* star history  — /repos/{o}/{r}/stargazers/history returns exact daily star counts
  grouped by week, most recent first (30 weeks per page). Since 2026-06-30 GitHub
  restricts the per-user /stargazers list to repo collaborators, so the privacy-safe
  history endpoint (2026-09) is the only source. The series is anchored on the
  current stargazers_count, so even the first page alone gives exact recent values.
* commits/week  — /stats/commit_activity (52 weeks); /stats/participation is used
  only as a fallback for the acceleration signal when the former is still computing.
* contributors  — /stats/contributors gives each author's first commit week.

Pure helpers (``parse_*``, ``star_history_to_series``, ``series_deltas``,
``build_*_signal(s)``) are network-free and unit tested; ``GitHubProvider``
only orchestrates HTTP calls.
"""

from __future__ import annotations

import hashlib
import html
import math
import re
import time
from bisect import bisect_right
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from radar import clock
from radar.providers.base import (
    CompanyRecord,
    EntityRef,
    MetricPoint,
    NormalizedSignal,
    PersonRecord,
    ProviderBatch,
    Relation,
    RepoRecord,
    domain_ref,
    gh_org_ref,
    gh_user_ref,
    repo_ref,
)
from radar.providers.domains import company_domain, is_academic_domain, registrable_domain
from radar.providers.http import HttpClient, content_hash
from radar.settings import get_settings

API = "https://api.github.com"
WEB = "https://github.com"
ACCEPT_JSON = "application/vnd.github+json"
ACCEPT_RAW = "application/vnd.github.raw"
API_VERSION = "2022-11-28"

STAR_HISTORY_MAX_PAGES = 40  # 30 weeks/page -> ~23 years; anonymous access fetches only the first page
SERIES_MAX_POINTS = 400
SERIES_KEEP_RECENT_DAYS = 90
STATS_BACKOFF = (2.0, 4.0, 8.0)  # retries for 202 "statistics are being computed"
README_CHARS = 2000

# Signal thresholds (kept here so tests and reviewers see one source of truth).
MIN_STARS_30D = 25
ACCEL_RATIO = 2.0
ACCEL_MIN_7D = 20
COMMIT_ACCEL_RATIO = 1.8
COMMIT_ACCEL_MIN = 20
MIN_NEW_CONTRIBUTORS = 2
NEW_CONTRIBUTOR_DAYS = 30
CREATED_WINDOW_DAYS = 365
RELEASE_WINDOW_DAYS = 90
YOUNG_REPO_DAYS = 180  # repos/orgs this young get the /commits?author fallback when stats are unavailable
COMMITS_FALLBACK_TOP = 6  # contributors probed per repo with /commits?author=<login>&per_page=1
STATS_RETRY_ATTEMPTS = 4  # retry_stats(): re-requests after the first try ...
STATS_RETRY_SPACING = 10.0  # ... spaced this many seconds apart
STATS_ENDPOINTS = ("commit_activity", "contributors")
CONTRIBUTOR_DATES_BASIS = "github stats/contributors (start of the UTC week of the first/last commit)"
LAST_COMMIT_BASIS = "github commits?author= (newest commit on the default branch)"
APPROX_FIRST_COMMIT_BASIS = ("approximate: repository created_at; the contributor's first commit is unknown "
                             "and history may predate the repository")

BOT_LOGINS = {
    "dependabot", "dependabot-preview", "renovate", "renovate-bot", "github-actions", "pre-commit-ci",
    "allcontributors", "all-contributors", "greenkeeper", "snyk-bot", "codecov", "imgbot", "mergify",
    "semantic-release-bot", "copilot", "web-flow", "actions-user", "deepsource-autofix",
    # AI coding agents commit as GitHub users; they are tools, never team members or founders.
    "claude", "claude-code", "claude-bot", "copilot-swe-agent", "copilot-pull-request-reviewer",
    "devin-ai-integration", "cursoragent", "cursor-agent", "cursor", "codex", "chatgpt-codex-connector",
    "openai-codex", "gemini-code-assist", "google-labs-jules", "jules", "sweep-ai", "coderabbitai",
    "sourcery-ai", "ellipsis-dev", "aider", "aider-chat", "cline", "openhands-agent", "amazon-q-developer",
}

SeriesPoint = tuple[date, float]


class GitHubAPIError(RuntimeError):
    def __init__(self, status: int, url: str, message: str = "") -> None:
        super().__init__(f"GitHub {status} for {url}: {message}".strip())
        self.status = status
        self.url = url


class GitHubRateLimitError(GitHubAPIError):
    """Primary rate limit exhausted; callers should stop issuing requests."""


# ── small parsing utilities ──────────────────────────────────────────────────


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _week_day(unix_ts: int) -> date:
    return datetime.fromtimestamp(int(unix_ts), tz=UTC).date()


def _clean(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    v = value.strip()
    return v or None


def log_scale(x: float, cap: float) -> float:
    """Bounded log scaling of a non-negative magnitude into 0..1 (x >= cap -> 1)."""
    if x <= 0 or cap <= 0:
        return 0.0
    return round(min(1.0, math.log1p(x) / math.log1p(cap)), 4)


def _ratio_strength(ratio: float, cap: float = 10.0) -> float:
    if ratio <= 1:
        return 0.0
    return min(1.0, math.log(ratio) / math.log(cap))


def is_bot(login: str | None, user_type: str | None = None) -> bool:
    if not login:
        return True
    lg = login.lower()
    return (
        (user_type is not None and user_type != "User")
        or lg.endswith(("[bot]", "-bot"))
        or lg in BOT_LOGINS
    )


def strip_markup(text: str | None, limit: int = README_CHARS) -> str | None:
    """Reduce README markdown/HTML/rST to readable plain text, truncated on a word boundary."""
    if not text:
        return None
    t = re.sub(r"<!--.*?-->", " ", text, flags=re.DOTALL)
    t = re.sub(r"(```|~~~).*?\1", " ", t, flags=re.DOTALL)  # fenced code
    t = re.sub(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)", " ", t)  # linked badges
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)  # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # links -> text
    t = re.sub(r"^\s*\[[^\]]+\]:\s*\S+.*$", " ", t, flags=re.MULTILINE)  # reference link defs
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<[^>]+>", " ", t)
    t = html.unescape(t)
    t = re.sub(r"^\s{0,3}#{1,6}\s*", "", t, flags=re.MULTILINE)  # headings
    t = re.sub(r"^\s*([-=*_|:+]\s*){3,}$", " ", t, flags=re.MULTILINE)  # rules / table separators
    t = re.sub(r"(\*\*|__|\*|`)", "", t)
    t = t.replace("|", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in t.splitlines()]
    out = re.sub(r"\n{2,}", "\n", "\n".join(ln for ln in lines if ln)).strip()
    if len(out) > limit:
        cut = out[:limit]
        out = cut[: cut.rfind(" ")] if " " in cut[limit // 2 :] else cut
        out = out.rstrip() + " …"
    return out or None


# ── record parsers ───────────────────────────────────────────────────────────


def parse_repo(data: Mapping[str, Any]) -> RepoRecord:
    """GET /repos/{owner}/{repo} JSON -> RepoRecord (readme/owner_company filled by caller)."""
    owner = data.get("owner") or {}
    lic = data.get("license") or {}
    license_id = lic.get("spdx_id")
    if license_id in (None, "NOASSERTION"):
        license_id = lic.get("name")
    full = data["full_name"]
    return RepoRecord(
        ref=repo_ref(full),
        full_name=full,
        owner=owner.get("login") or full.split("/")[0],
        owner_type=owner.get("type"),
        description=_clean(data.get("description")),
        homepage=_clean(data.get("homepage")),
        language=data.get("language"),
        topics=list(data.get("topics") or []),
        stars=int(data.get("stargazers_count") or 0),
        forks=int(data.get("forks_count") or 0),
        watchers=int(data.get("subscribers_count") or 0),
        open_issues=int(data.get("open_issues_count") or 0),
        gh_created_at=_dt(data.get("created_at")),
        pushed_at=_dt(data.get("pushed_at")),
        license=license_id,
        archived=bool(data.get("archived")),
        attrs={
            "html_url": data.get("html_url") or f"{WEB}/{full}",
            "fork": bool(data.get("fork")),
            "parent": (data.get("parent") or {}).get("full_name"),
            "default_branch": data.get("default_branch"),
            "size_kb": data.get("size"),
            "gh_id": data.get("id"),
        },
    )


def _org_domain(login: str, blog: str | None) -> str | None:
    """Company domain from an org's website. Academic domains never identify a company
    (they would merge every lab of a university). A platform domain is accepted only for
    the platform's own org (e.g. org 'huggingface' -> huggingface.co)."""
    dom = company_domain(blog)
    if dom is None:
        reg = registrable_domain(blog)
        if reg and re.sub(r"[^a-z0-9]", "", reg.split(".")[0]) == re.sub(r"[^a-z0-9]", "", login.lower()):
            dom = reg
    if dom and is_academic_domain(dom):
        return None
    return dom


def parse_org(data: Mapping[str, Any]) -> CompanyRecord:
    """GET /orgs/{org} JSON -> CompanyRecord keyed by github_org (domain as alt ref)."""
    login = data["login"]
    blog = _clean(data.get("blog"))
    dom = _org_domain(login, blog)
    academic = bool(registrable_domain(blog)) and is_academic_domain(registrable_domain(blog))
    x_handle = _clean(data.get("twitter_username"))
    alt: list[EntityRef] = []
    if dom:
        alt.append(domain_ref(dom))
    if x_handle:
        alt.append(EntityRef(type="company", key=f"x:{x_handle.lower()}"))
    created = _dt(data.get("created_at"))
    return CompanyRecord(
        ref=gh_org_ref(login),
        name=_clean(data.get("name")) or login,
        description=_clean(data.get("description")),
        website=blog if (dom or academic) else None,
        github_org=login,
        x_handle=x_handle,
        location=_clean(data.get("location")),
        kind="company" if dom else ("lab" if academic else "project"),
        founded_date=created.date() if created else None,
        alt_refs=alt,
        attrs={
            "gh_org_created_at": created.isoformat() if created else None,
            "public_repos": data.get("public_repos"),
            "followers": data.get("followers"),
            "blog": blog,
            "founded_date_basis": "github_org_created_at",
        },
        source_url=data.get("html_url") or f"{WEB}/{login}",
    )


def parse_user(data: Mapping[str, Any]) -> PersonRecord:
    """GET /users/{login} JSON -> PersonRecord."""
    login = data["login"]
    company = _clean(data.get("company"))
    if company:
        company = re.sub(r"(^|\s)@", r"\1", company).strip() or None
    x_handle = _clean(data.get("twitter_username"))
    alt = [EntityRef(type="person", key=f"x:{x_handle.lower()}")] if x_handle else []
    # Profile fields are typed by the user themselves: label them so the UI never presents them as verified.
    reported = [f for f in ("company", "bio", "blog", "twitter_username", "location") if _clean(data.get(f))]
    return PersonRecord(
        ref=gh_user_ref(login),
        name=_clean(data.get("name")) or login,
        bio=_clean(data.get("bio")),
        location=_clean(data.get("location")),
        github_handle=login,
        x_handle=x_handle,
        personal_site=_clean(data.get("blog")),
        current_company=company,
        followers=data.get("followers"),
        alt_refs=alt,
        attrs={
            "public_repos": data.get("public_repos"),
            "gh_created_at": data.get("created_at"),
            "avatar_url": data.get("avatar_url"),
            "hireable": data.get("hireable"),
            "self_reported": True,
            "self_reported_fields": reported,
        },
        source_url=data.get("html_url") or f"{WEB}/{login}",
    )


# ── star history ─────────────────────────────────────────────────────────────


@dataclass
class StarHistory:
    """Exact cumulative star counts at the end of each day on which the count changed.

    complete: the series reaches back to the repository's first star, so any day before
              the first point has 0 stars. Otherwise days before the first point (the
              coverage boundary) are unknown.
    """

    points: list[SeriesPoint] = field(default_factory=list)
    complete: bool = True

    def value_at(self, day: date) -> float | None:
        pts = self.points
        if not pts or day < pts[0][0]:
            return 0.0 if self.complete else None
        i = bisect_right(pts, (day, math.inf)) - 1
        return pts[i][1]


def star_history_to_series(
    weeks: Sequence[Mapping[str, Any]], total_stars: int, *, complete: bool, today: date | None = None,
) -> StarHistory:
    """/stargazers/history weeks (``{week: unix Sunday, days: [7 daily counts]}``) -> cumulative series.

    The series is anchored on the repo's current ``stargazers_count`` and walked backwards,
    so it is exact even when only the most recent pages were fetched: the cumulative count
    at the end of day D is ``total - stars gained after D``. Days after ``today`` are ignored.
    """
    daily: dict[date, float] = {}
    for w in weeks:
        if "week" not in w:
            continue
        start = _week_day(w["week"])
        for i, n in enumerate(w.get("days") or []):
            d = start + timedelta(days=i)
            if (today is None or d <= today) and n:
                daily[d] = daily.get(d, 0.0) + float(n)
    if not weeks:
        return StarHistory(points=[], complete=complete)
    oldest = min(_week_day(w["week"]) for w in weeks if "week" in w)
    remaining = float(total_stars)
    rev: list[SeriesPoint] = []
    for d in sorted(daily, reverse=True):
        rev.append((d, remaining))
        remaining -= daily[d]
    if not complete:
        rev.append((oldest - timedelta(days=1), remaining))  # exact value at the coverage boundary
    return StarHistory(points=rev[::-1], complete=complete)


def compact_series(
    points: Sequence[SeriesPoint], *, max_points: int = SERIES_MAX_POINTS,
    keep_recent_days: int = SERIES_KEEP_RECENT_DAYS,
) -> list[SeriesPoint]:
    """Keep every point in the recent window; thin older points evenly (first point kept).
    Each kept point remains an exact cumulative value for its day."""
    pts = list(points)
    if len(pts) <= max_points:
        return pts
    cutoff = pts[-1][0] - timedelta(days=keep_recent_days)
    recent = [p for p in pts if p[0] > cutoff][-max_points:]
    old = [p for p in pts if p[0] <= cutoff]
    budget = max_points - len(recent)
    if budget <= 0 or not old:
        return recent
    if len(old) > budget:
        step = (len(old) - 1) / max(budget - 1, 1)
        old = [old[round(i * step)] for i in range(budget)] if budget > 1 else [old[0]]
    return old + recent


def series_deltas(history: StarHistory, today: date) -> dict[str, float] | None:
    """Star gains over trailing windows; prev_28d covers days today-35..today-7.
    None when the history does not reach back 35 days."""
    now_v = history.value_at(today)
    v7 = history.value_at(today - timedelta(days=7))
    v30 = history.value_at(today - timedelta(days=30))
    v35 = history.value_at(today - timedelta(days=35))
    if None in (now_v, v7, v30, v35):
        return None
    assert now_v is not None and v7 is not None and v30 is not None and v35 is not None
    prev_28d = v7 - v35
    return {
        "stars_now": now_v,
        "stars_7d": now_v - v7,
        "stars_30d": now_v - v30,
        "stars_30d_ago": v30,
        "prev_28d": prev_28d,
        "prev_weekly_rate": round(prev_28d / 4, 2),
    }


# ── stats endpoints ──────────────────────────────────────────────────────────


def commit_activity_to_points(data: Sequence[Mapping[str, Any]], entity: EntityRef) -> list[MetricPoint]:
    """/stats/commit_activity -> weekly commit counts (day = week start, Sunday UTC)."""
    return [
        MetricPoint(entity=entity, metric="commits_week", day=_week_day(w["week"]), value=float(w.get("total", 0)),
                    source="backfill")
        for w in data
        if "week" in w
    ]


def contributor_stats_to_spans(data: Sequence[Mapping[str, Any]]) -> dict[str, tuple[date, date]]:
    """/stats/contributors -> {login: (week of first commit, week of last commit)}.

    Weeks are GitHub's Sunday-start UTC weeks; only weeks with at least one commit count.
    Bots and deleted users are skipped."""
    out: dict[str, tuple[date, date]] = {}
    for c in data:
        author = c.get("author") or {}
        login = author.get("login")
        if is_bot(login, author.get("type")):
            continue
        weeks = [w["w"] for w in c.get("weeks") or [] if "w" in w and w.get("c", 0) > 0]
        if weeks:
            out[login] = (_week_day(min(weeks)), _week_day(max(weeks)))
    return out


def contributor_stats_to_first_weeks(data: Sequence[Mapping[str, Any]]) -> dict[str, date]:
    """/stats/contributors -> {login: week of first commit}. Bots and deleted users skipped."""
    return {lg: span[0] for lg, span in contributor_stats_to_spans(data).items()}


def _week_start(d: date) -> str:
    return datetime(d.year, d.month, d.day, tzinfo=UTC).isoformat()


def contributor_date_attrs(first: date, last: date) -> dict[str, Any]:
    """Relation attrs for a contributor's commit span from /stats/contributors.

    Both values are the *start* of the (UTC, Sunday) week holding the first/last commit: the
    first is exact to the week, the last is a lower bound within its week."""
    return {"first_commit_week": first.isoformat(), "first_commit_at": _week_start(first),
            "last_commit_at": _week_start(last), "commit_dates_basis": CONTRIBUTOR_DATES_BASIS}


def last_commit_from_commits(data: Any) -> datetime | None:
    """Newest commit date from ``/commits?author=<login>&per_page=1`` (author date, else committer)."""
    if not isinstance(data, list) or not data or not isinstance(data[0], Mapping):
        return None
    commit = data[0].get("commit") or {}
    for who in ("author", "committer"):
        try:
            at = _dt((commit.get(who) or {}).get("date"))
        except ValueError:
            at = None
        if at is not None:
            return at
    return None


def contributors_cumulative_points(first_weeks: Mapping[str, date], entity: EntityRef) -> list[MetricPoint]:
    """Cumulative distinct contributors by first-commit week (one point per week with a change)."""
    counts: dict[date, int] = {}
    for d in first_weeks.values():
        counts[d] = counts.get(d, 0) + 1
    out: list[MetricPoint] = []
    total = 0
    for d in sorted(counts):
        total += counts[d]
        out.append(MetricPoint(entity=entity, metric="contributors", day=d, value=float(total), source="backfill"))
    return out


def new_contributors(first_weeks: Mapping[str, date], today: date, days: int = NEW_CONTRIBUTOR_DAYS) -> list[str]:
    """Logins whose first commit week started within ``days`` of today, excluding the
    repo's founding cohort (everyone in the earliest week is a founder, not a newcomer)."""
    if not first_weeks:
        return []
    founding = min(first_weeks.values())
    cutoff = today - timedelta(days=days)
    return sorted(lg for lg, d in first_weeks.items() if d >= cutoff and d > founding)


# ── signal builders ──────────────────────────────────────────────────────────


def _event_now() -> datetime:
    return clock.now()


def build_velocity_signals(
    repo: RepoRecord, history: StarHistory, today: date, *, related: Sequence[EntityRef] = (),
) -> list[NormalizedSignal]:
    """star_velocity and star_acceleration from the exact daily star series."""
    d = series_deltas(history, today)
    if d is None:
        return []
    # The /stargazers web page is collaborator-only since 2026-06; the repo page shows the count.
    url = repo.attrs.get("html_url") or f"{WEB}/{repo.full_name}"
    common: dict[str, Any] = {"entity": repo.ref, "source_type": "github", "source_url": url,
                              "event_at": _event_now(), "related": list(related)}
    meta = {**d, "full_name": repo.full_name, "basis": "github stargazers/history (daily counts)"}
    out: list[NormalizedSignal] = []
    if d["stars_30d"] >= MIN_STARS_30D:
        out.append(NormalizedSignal(
            **common, signal_type="star_velocity",
            title=f"{repo.full_name}: +{d['stars_30d']:,.0f} stars in 30 days",
            description=(f"+{d['stars_30d']:,.0f} stars in 30 days (from {d['stars_30d_ago']:,.0f} to "
                         f"{d['stars_now']:,.0f}); +{d['stars_7d']:,.0f} in the last 7 days."),
            raw_value=d["stars_30d"], normalized_value=log_scale(d["stars_30d"], 3000), metadata=meta,
        ))
    created = repo.gh_created_at.date() if repo.gh_created_at else None
    old_enough = created is None or (today - created).days >= 35  # a prior window must exist
    prev_rate = d["prev_weekly_rate"]
    if old_enough and d["stars_7d"] >= ACCEL_MIN_7D and d["stars_7d"] > ACCEL_RATIO * prev_rate:
        ratio = d["stars_7d"] / prev_rate if prev_rate > 0 else None
        strength = 0.5 * log_scale(d["stars_7d"], 1000) + 0.5 * (_ratio_strength(ratio) if ratio else 1.0)
        ratio_txt = f"{ratio:.1f}x" if ratio else "from a standing start"
        out.append(NormalizedSignal(
            **common, signal_type="star_acceleration",
            title=f"{repo.full_name}: star growth accelerating ({ratio_txt})",
            description=(f"+{d['stars_7d']:,.0f} stars in the last 7 days vs {prev_rate:,.1f}/week "
                         f"over the previous 28 days."),
            raw_value=d["stars_7d"], normalized_value=round(strength, 4),
            metadata={**meta, "ratio": round(ratio, 2) if ratio else None},
        ))
    return out


def build_commit_acceleration_signal(
    repo: RepoRecord, weekly_counts: Sequence[float], *, basis: str = "stats/commit_activity",
    related: Sequence[EntityRef] = (),
) -> NormalizedSignal | None:
    """Weekly commit counts oldest->newest. Last 4 weeks (incl. the current partial week,
    which is conservative) vs the 4 weeks before."""
    if len(weekly_counts) < 8:
        return None
    recent = float(sum(weekly_counts[-4:]))
    prev = float(sum(weekly_counts[-8:-4]))
    if recent < COMMIT_ACCEL_MIN or recent <= COMMIT_ACCEL_RATIO * prev:
        return None
    ratio = recent / prev if prev > 0 else None
    strength = 0.5 * log_scale(recent, 500) + 0.5 * (_ratio_strength(ratio) if ratio else 1.0)
    return NormalizedSignal(
        entity=repo.ref, signal_type="commit_acceleration", source_type="github",
        source_url=f"{WEB}/{repo.full_name}/graphs/commit-activity",
        title=f"{repo.full_name}: commit pace {f'{ratio:.1f}x' if ratio else 'up from zero'} vs prior 4 weeks",
        description=f"{recent:.0f} commits in the last 4 weeks vs {prev:.0f} in the 4 weeks before.",
        raw_value=recent, normalized_value=round(strength, 4), event_at=_event_now(), related=list(related),
        metadata={"commits_4w": recent, "commits_prev_4w": prev, "ratio": round(ratio, 2) if ratio else None,
                  "basis": basis},
    )


def build_new_contributors_signal(
    repo: RepoRecord, logins: Sequence[str], first_weeks: Mapping[str, date], *, stats_truncated: bool = False,
) -> NormalizedSignal | None:
    if len(logins) < MIN_NEW_CONTRIBUTORS:
        return None
    return NormalizedSignal(
        entity=repo.ref, signal_type="new_contributors", source_type="github",
        source_url=f"{WEB}/{repo.full_name}/graphs/contributors",
        title=f"{repo.full_name}: {len(logins)} new contributors in the last {NEW_CONTRIBUTOR_DAYS} days",
        description="First commits by: " + ", ".join(logins[:12]) + ("…" if len(logins) > 12 else "") + ".",
        raw_value=float(len(logins)), normalized_value=log_scale(len(logins), 20), event_at=_event_now(),
        related=[gh_user_ref(lg) for lg in logins],
        metadata={"logins": list(logins), "first_weeks": {lg: first_weeks[lg].isoformat() for lg in logins},
                  "stats_top100_only": stats_truncated},
    )


def build_release_signals(
    repo: RepoRecord, releases: Sequence[Mapping[str, Any]], today: date, *, window_days: int = RELEASE_WINDOW_DAYS,
) -> list[NormalizedSignal]:
    published = [r for r in releases if not r.get("draft") and _dt(r.get("published_at"))]
    oldest = min(published, key=lambda r: r["published_at"]) if published and len(releases) < 20 else None
    out: list[NormalizedSignal] = []
    for r in published:
        at = _dt(r["published_at"])
        assert at is not None
        if (today - at.date()).days > window_days:
            continue
        tag = r.get("tag_name") or r.get("name") or "release"
        first = r is oldest
        body = " ".join((strip_markup(r.get("body"), 300) or "").split())
        desc = (r.get("name") or tag) + (" (pre-release)" if r.get("prerelease") else "")
        if first:
            desc += " — first published release"
        out.append(NormalizedSignal(
            entity=repo.ref, signal_type="release_published", source_type="github",
            source_url=r.get("html_url") or f"{WEB}/{repo.full_name}/releases/tag/{tag}",
            title=f"{repo.full_name}: Released {tag}", description=desc + (f". {body}" if body else ""),
            normalized_value=0.6 if first else (0.25 if r.get("prerelease") else 0.4), event_at=at,
            metadata={"tag": tag, "prerelease": bool(r.get("prerelease")), "first_release": first},
        ))
    return out


def build_creation_signals(
    repo: RepoRecord, company: CompanyRecord | None, today: date,
) -> list[NormalizedSignal]:
    out: list[NormalizedSignal] = []
    if repo.gh_created_at and (age := (today - repo.gh_created_at.date()).days) <= CREATED_WINDOW_DAYS:
        out.append(NormalizedSignal(
            entity=repo.ref, signal_type="repo_created", source_type="github",
            source_url=repo.attrs.get("html_url") or f"{WEB}/{repo.full_name}",
            title=f"Repository {repo.full_name} created",
            description=(f"Created {repo.gh_created_at.date().isoformat()} ({age} days ago); "
                         f"{repo.stars:,} stars now." + (f" {repo.description}" if repo.description else "")),
            raw_value=float(repo.stars), normalized_value=round(max(0.05, 1 - age / CREATED_WINDOW_DAYS), 4),
            event_at=repo.gh_created_at, related=[company.ref] if company else [],
            metadata={"age_days": age, "stars": repo.stars},
        ))
    created_iso = company.attrs.get("gh_org_created_at") if company else None
    if company and created_iso:
        created = datetime.fromisoformat(created_iso)
        age = (today - created.date()).days
        if age <= CREATED_WINDOW_DAYS:
            out.append(NormalizedSignal(
                entity=company.ref, signal_type="org_created", source_type="github",
                source_url=company.source_url, title=f"GitHub org {company.github_org} created",
                description=f"GitHub organization '{company.name}' created {created.date().isoformat()} ({age} days ago).",
                normalized_value=round(max(0.05, 1 - age / CREATED_WINDOW_DAYS), 4), event_at=created,
                related=[repo.ref], metadata={"age_days": age},
            ))
    return out


def build_search_query(query: str, *, created_after: date | None = None, pushed_after: date | None = None,
                       min_stars: int = 0) -> str:
    q = f'"{query.replace(chr(34), "")}" in:name,description,topics,readme'
    if created_after:
        q += f" created:>{created_after.isoformat()}"
    if pushed_after:
        q += f" pushed:>{pushed_after.isoformat()}"
    if min_stars:
        q += f" stars:>={min_stars}"
    return q + " fork:false archived:false"


def _link_rel(link: str | None, rel: str) -> str | None:
    """URL for ``rel`` from an RFC 5988 Link header."""
    for part in (link or "").split(","):
        m = re.match(r'\s*<([^>]+)>;\s*rel="([^"]+)"', part)
        if m and m.group(2) == rel:
            return m.group(1)
    return None


def _last_page_from_link(link: str | None) -> int | None:
    m = re.search(r"[?&]page=(\d+)", _link_rel(link, "last") or "")
    return int(m.group(1)) if m else None


def readme_sha(raw: str | None) -> str | None:
    """sha256 of the raw README text (before markup stripping): changes whenever the file changes."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else None


def release_points(
    releases: Sequence[Mapping[str, Any]], total: int, entity: EntityRef, today: date, *, complete: bool,
) -> list[MetricPoint]:
    """Cumulative published releases per day (backfill, days before ``today``), from release dates.

    Anchored on the current ``total`` and walked backwards like the star history, so it is exact
    for every day on or after the oldest fetched release even when only the newest page was read.
    Drafts are not public releases and are not counted. With ``complete`` (every release fetched)
    the boundary point before the first release is 0."""
    days: dict[date, int] = {}
    for r in releases:
        at = _dt(r.get("published_at"))
        if r.get("draft") or at is None:
            continue
        days[at.date()] = days.get(at.date(), 0) + 1
    if not days:
        return []
    remaining = float(total)
    rev: list[tuple[date, float]] = []
    for d in sorted(days, reverse=True):
        if d < today:
            rev.append((d, remaining))
        remaining -= days[d]
    # The day before the oldest fetched release is exact too (0 when every release was fetched).
    rev.append((min(days) - timedelta(days=1), 0.0 if complete else max(remaining, 0.0)))
    return [MetricPoint(entity=entity, metric="releases_total", day=d, value=v, source="backfill")
            for d, v in reversed(rev)]


# ── young-organization discovery (pure helpers) ──────────────────────────────

YOUNG_ORG_SEARCH_BUDGET = 20
YOUNG_ORG_SCREEN_CALLS = 6  # core calls budgeted per screened org
YOUNG_ORG_TOP_REPOS = 2  # repos whose contributors are screened
YOUNG_ORG_STAR_PAGES = 3  # 90 weeks of star history covers any young repo
SEARCH_RESULT_CAP = 1000  # GitHub search never returns more than this per query

# Orgs that are coursework, events or curated lists — not companies in formation. Logins/names are
# matched on whole words; descriptions only on unambiguous phrases ("world-class", "teaching robots"
# and "of course" must not trip the filter).
NOISE_NAME_RE = re.compile(
    r"\b(awesome|curated|course|courses|coursework|classroom|lecture|lectures|homework|assignments?|students?|"
    r"syllabus|bootcamp|hackathons?|hackweek|game ?jam|workshops?|tutorials?|university|univ|college|school|"
    r"faculty|department|cs ?\d{3}|\d{4} ?(?:spring|fall|summer|winter)|(?:spring|fall|summer|winter) ?\d{2,4})\b",
    re.IGNORECASE,
)
NOISE_DESC_RE = re.compile(
    r"\b(awesome list|curated list|course materials?|coursework|homework|assignments? for|lecture notes|"
    r"class projects?|student projects?|students? of|hackathon|bootcamp|university|universit[äa]t|"
    r"teaching materials?|tutorials? for)\b",
    re.IGNORECASE,
)
LIST_REPO_RE = re.compile(
    r"(^|[-_.])awesome($|[-_.])|curated list|reading list|paper list|list of (papers|resources|tools)|"
    r"interview", re.IGNORECASE,
)


def build_young_org_query(term: str, start: date, end: date | None = None, *, min_stars: int = 3) -> str:
    """search/repositories query for repos created in [start, end] (open-ended when ``end`` is None)."""
    t = term.replace('"', "").strip()
    t = f'"{t}"' if " " in t else t
    created = f"created:{start.isoformat()}..{end.isoformat()}" if end else f"created:>={start.isoformat()}"
    return f"{t} {created} stars:>={min_stars} fork:false"


def _words(*parts: str | None) -> str:
    # Split camelCase / kebab / snake so word boundaries work on logins like "ml-course-2026".
    text = " ".join(p for p in parts if p)
    return re.sub(r"[-_./]+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", text))


def noise_org_reason(login: str, name: str | None = None, description: str | None = None,
                     blog: str | None = None) -> str | None:
    """Why an org is not a company in formation (None = keep). Uses only public org fields."""
    if is_bot(login):
        return "bot"
    if (reg := registrable_domain(blog)) and is_academic_domain(reg):
        return "academic"
    m = NOISE_NAME_RE.search(_words(login, name)) or NOISE_DESC_RE.search(description or "")
    if m:
        word = m.group(0).lower()
        return "list" if ("awesome" in word or "curated" in word) else "course_or_event"
    return None


def is_list_repo(item: Mapping[str, Any]) -> bool:
    """awesome-lists, reading lists and interview prep: popular, but not products."""
    return bool(LIST_REPO_RE.search(item.get("name") or "") or LIST_REPO_RE.search(item.get("description") or ""))


def qualifying_members(contributors: Mapping[str, Sequence[Mapping[str, Any]]], *, min_commits: int,
                       ) -> dict[str, dict[str, Any]]:
    """{repo full_name: /contributors list} -> non-bot logins with >= ``min_commits`` across the repos.

    ``contributions`` is GitHub's commit count on the default branch."""
    totals: dict[str, dict[str, Any]] = {}
    for full, rows in contributors.items():
        for c in rows:
            login = c.get("login")
            if is_bot(login, c.get("type")):
                continue
            m = totals.setdefault(login.lower(), {"login": login, "commits": 0, "repos": {}})
            n = int(c.get("contributions") or 0)
            m["commits"] += n
            m["repos"][full] = n
    return {k: v for k, v in sorted(totals.items(), key=lambda kv: -kv[1]["commits"])
            if v["commits"] >= min_commits}


def build_young_org_team_signal(
    company: CompanyRecord, members: Sequence[Mapping[str, Any]], *, age_days: int, age_basis: str,
    created_on: date, min_commits: int, window_days: int, repos: Sequence[str],
    related_logins: Sequence[str] | None = None,
) -> NormalizedSignal:
    """A young GitHub organization with a working team (counted from public commit counts).

    ``related_logins`` limits the person links to members whose profiles are in the batch."""
    n = len(members)
    login = company.github_org or company.name
    youth = max(0.0, 1 - age_days / max(window_days, 1))
    return NormalizedSignal(
        entity=company.ref, signal_type="young_org_team", source_type="github",
        source_url=company.source_url or f"{WEB}/{login}",
        title=f"{login}: {n} contributors with >={min_commits} commits in a {age_days}-day-old organization",
        description=(f"{n} non-bot contributors with at least {min_commits} commits each across "
                     f"{', '.join(repos)}; " + ("organization" if age_basis == "org_created_at"
                                                else "first public repository") +
                     f" created {created_on.isoformat()}. Members: " +
                     ", ".join(f"{m['login']} ({m['commits']})" for m in members[:10]) +
                     ("…" if n > 10 else "") + "."),
        raw_value=float(n), normalized_value=round(0.5 * log_scale(n, 8) + 0.5 * youth, 4),
        event_at=_event_now(),
        related=[gh_user_ref(lg) for lg in (related_logins if related_logins is not None
                                             else [m["login"] for m in members])] + [repo_ref(r) for r in repos],
        metadata={"members": [{"login": m["login"], "commits": m["commits"], "repos": dict(m["repos"])}
                              for m in members],
                  "age_days": age_days, "age_basis": age_basis, "created_on": created_on.isoformat(),
                  "min_commits": min_commits, "repos": list(repos), "basis": "github /contributors commit counts"},
    )


def split_login(ref: EntityRef) -> str:
    """'github_user:alice' -> 'alice'."""
    return ref.key.split(":", 1)[1] if ":" in ref.key else ref.key


def _is_young(repo: RepoRecord, company: CompanyRecord | None, today: date, days: int = YOUNG_REPO_DAYS) -> bool:
    """An org-owned repo whose org (or the repo itself) was created within ``days``."""
    if company is None:
        return False
    created = [repo.gh_created_at]
    if iso := company.attrs.get("gh_org_created_at"):
        created.append(datetime.fromisoformat(iso))
    return any(c is not None and (today - c.date()).days <= days for c in created)


# ── provider ─────────────────────────────────────────────────────────────────


class GitHubProvider:
    name = "github"

    def __init__(
        self,
        client: HttpClient | None = None,
        token: str | None = None,
        mode: str = "live",
        cassette_dir: Path | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.token = token if token is not None else get_settings().resolved_github_token()
        # identity encoding: HttpClient rebuilds responses from already-decoded bytes but keeps the
        # Content-Encoding header, so a gzip body would be decoded twice (see report to foundation owner).
        self._headers = {"X-GitHub-Api-Version": API_VERSION, "Accept-Encoding": "identity"}
        if self.token:
            self._headers["Authorization"] = f"Bearer {self.token}"
        self.client = client or HttpClient(min_interval=0.1, cassette_dir=cassette_dir, mode=mode)
        self._sleep = sleep
        self.search_interval = 2.2 if self.token else 6.5  # 30/min authenticated, 10/min anonymous
        self._last_search = 0.0
        self.rate_limits: dict[str, int] = {}  # resource (core/search) -> remaining
        self._orgs: dict[str, dict[str, Any] | None] = {}
        self._users: dict[str, dict[str, Any] | None] = {}
        self.young_org_report: dict[str, list[str]] = {}  # rejection reason -> org logins (last discovery)
        # (repo full_name, stats endpoint) pairs that were still 202 "computing" when given up on; a later
        # fetch_pending_stats() / retry_stats() in the same process fills them in.
        self.pending_stats: list[tuple[str, str]] = []
        # Anonymous access (60 req/h) cannot afford deep history; degrade instead of failing.
        self.max_star_pages = 40 if self.token else 3
        self.max_contributors_cap = 30 if self.token else 3

    def enabled(self) -> tuple[bool, str]:
        if self.token:
            return True, "authenticated (5,000 requests/hour)"
        return True, "unauthenticated: 60 requests/hour and shallow history — set GITHUB_TOKEN (recommended)"

    # ── HTTP ──
    def _get(self, path: str, *, params: dict[str, Any] | None = None, accept: str = ACCEPT_JSON,
             search: bool = False) -> httpx.Response:
        url = path if path.startswith("http") else API + path
        for attempt in range(2):
            if search:
                wait = self.search_interval - (time.monotonic() - self._last_search)
                if wait > 0:
                    self._sleep(wait)
                self._last_search = time.monotonic()
            r = self.client.get(url, params=params, headers={**self._headers, "Accept": accept})
            remaining = r.headers.get("x-ratelimit-remaining")
            if remaining and remaining.isdigit():
                self.rate_limits[r.headers.get("x-ratelimit-resource", "search" if search else "core")] = int(remaining)
            if r.status_code in (403, 429):
                reset = r.headers.get("x-ratelimit-reset")
                if remaining == "0":
                    wait_s = (int(reset) - time.time() + 1) if reset and reset.isdigit() else None
                    if search and attempt == 0 and wait_s is not None and wait_s <= 65:
                        self._sleep(max(wait_s, 1))
                        continue
                    raise GitHubRateLimitError(r.status_code, url, f"rate limit exhausted (reset {reset})")
                ra = r.headers.get("retry-after")
                if attempt == 0 and ra and ra.isdigit() and int(ra) <= 60:  # secondary rate limit
                    self._sleep(int(ra))
                    continue
            return r
        return r

    def _json(self, path: str, **kw: Any) -> Any:
        r = self._get(path, **kw)
        if r.status_code >= 400:
            msg = ""
            try:
                msg = (r.json() or {}).get("message", "")
            except ValueError:
                pass
            raise GitHubAPIError(r.status_code, str(r.request.url), msg)
        return r.json() if r.content else None

    def _budget_ok(self, need: int) -> bool:
        rem = self.rate_limits.get("core")
        return rem is None or rem > need + 25

    def _stats(self, path: str, primed: httpx.Response | None, errors: list[str], label: str) -> list | None:
        """GitHub stats endpoints answer 202 while computing: retry with 2s/4s/8s backoff, then give up."""
        r = primed if primed is not None else self._get(path)
        for delay in STATS_BACKOFF:
            if r.status_code != 202:
                break
            self._sleep(delay)
            r = self._get(path)
        if r.status_code == 202:
            errors.append(f"{label}: statistics still being computed after {len(STATS_BACKOFF)} retries (202); "
                          "queued for a later retry")
            m = re.match(r"^/repos/([^/]+/[^/]+)/stats/([a-z_]+)$", path)
            if m and (m.group(1), m.group(2)) not in self.pending_stats:
                self.pending_stats.append((m.group(1), m.group(2)))
            return None
        if r.status_code == 204:
            return []
        if r.status_code >= 400:
            errors.append(f"{label}: HTTP {r.status_code}; skipped")
            return None
        try:
            data = r.json() if r.content else []
        except ValueError:  # body over the HttpClient size cap is truncated
            errors.append(f"{label}: response too large to parse; skipped")
            return None
        return data if isinstance(data, list) else []

    def _org(self, login: str) -> dict[str, Any] | None:
        key = login.lower()
        if key not in self._orgs:
            try:
                self._orgs[key] = self._json(f"/orgs/{login}")
            except GitHubRateLimitError:
                raise
            except GitHubAPIError as e:
                if e.status != 404:
                    raise
                self._orgs[key] = None
        return self._orgs[key]

    def _user(self, login: str) -> dict[str, Any] | None:
        key = login.lower()
        if key not in self._users:
            try:
                self._users[key] = self._json(f"/users/{login}")
            except GitHubRateLimitError:
                raise
            except GitHubAPIError as e:
                if e.status != 404:
                    raise
                self._users[key] = None
        return self._users[key]

    # ── search ──
    def _search(self, q: str, sort: str, limit: int) -> list[str]:
        data = self._json("/search/repositories", params={"q": q, "sort": sort, "order": "desc",
                                                          "per_page": min(max(limit, 1), 100)}, search=True)
        return [it["full_name"] for it in (data or {}).get("items", [])[:limit]]

    def search_repos(self, query: str, *, created_within_days: int = 150, min_stars: int = 15, limit: int = 20,
                     sort: str = "stars") -> list[str]:
        """Recently created repos matching a thesis phrase, most-starred first."""
        after = clock.today() - timedelta(days=created_within_days)
        return self._search(build_search_query(query, created_after=after, min_stars=min_stars), sort, limit)

    def search_recently_pushed(self, query: str, *, pushed_within_days: int = 30, min_stars: int = 50,
                               limit: int = 20) -> list[str]:
        """Repos of any age with recent pushes (catches old repos waking up)."""
        after = clock.today() - timedelta(days=pushed_within_days)
        return self._search(build_search_query(query, pushed_after=after, min_stars=min_stars), "updated", limit)

    # ── fetch ──
    def _readme(self, full: str) -> tuple[str | None, str | None]:
        """(plain-text excerpt, sha256 of the raw README)."""
        r = self._get(f"/repos/{full}/readme", accept=ACCEPT_RAW)
        if r.status_code != 200:
            return None, None
        return strip_markup(r.text), readme_sha(r.text)

    def _count_via_link(self, path: str, params: dict[str, Any]) -> int | None:
        """Item count of a list endpoint in one call: per_page=1 and the Link rel=last page number."""
        r = self._get(path, params={**params, "per_page": 1})
        if r.status_code != 200:
            return None
        last = _last_page_from_link(r.headers.get("link"))
        if last is not None:
            return last
        try:
            data = r.json() if r.content else []
        except ValueError:
            return None
        return len(data) if isinstance(data, list) else None

    def _star_history(self, full: str, stars: int, errors: list[str], max_pages: int | None = None,
                      ) -> StarHistory | None:
        """Walk /stargazers/history (newest week first) via Link rel=next."""
        if stars <= 0:
            return None
        cap = STAR_HISTORY_MAX_PAGES if self.token else 1
        max_pages = min(max_pages, cap) if max_pages else cap
        url: str | None = f"/repos/{full}/stargazers/history"
        weeks: list[dict[str, Any]] = []
        complete = False
        for _ in range(max_pages):
            if url is None or not self._budget_ok(1):
                break
            r = self._get(url)
            if r.status_code >= 400:
                errors.append(f"{full}: stargazers/history HTTP {r.status_code}")
                break
            page = r.json() if r.content else []
            weeks.extend(page or [])
            url = _link_rel(r.headers.get("link"), "next")
            if not page or url is None:
                complete = True
                break
        if not weeks:
            return None
        return star_history_to_series(weeks, stars, complete=complete, today=clock.today())

    def fetch_repo(self, full_name: str, *, deep: bool = True, max_contributors: int = 12,
                   star_pages: int | None = None) -> ProviderBatch:
        """Repository + owner + (deep) contributors, real history series and change signals.

        ``star_pages`` caps the star-history walk (young repos need few pages)."""
        batch = ProviderBatch(provider=self.name)
        today = clock.today()
        data = self._json(f"/repos/{full_name}")
        repo = parse_repo(data)
        full = repo.full_name
        html_url = repo.attrs["html_url"]
        repo.readme_excerpt, sha = self._readme(full)
        if sha:
            repo.attrs["readme_sha"] = sha  # raw-content hash: the pipeline diffs it to detect README changes
        # open_issues_count includes pull requests; split them with one Link-header count call.
        open_prs = self._count_via_link(f"/repos/{full}/pulls", {"state": "open"})
        if open_prs is not None:
            repo.attrs["open_prs"] = open_prs
            repo.attrs["open_issues_basis"] = "issues_only"
            repo.open_issues = max(0, repo.open_issues - open_prs)

        company: CompanyRecord | None = None
        owner_person: PersonRecord | None = None
        if repo.owner_type == "Organization":
            org = self._org(repo.owner)
            if org:
                company = parse_org(org)
                repo.owner_company = company.ref
                batch.companies.append(company)
                batch.relations.append(Relation(kind="owns", src=company.ref, dst=repo.ref, source_url=html_url))
        elif repo.owner_type == "User":
            u = self._user(repo.owner)
            if u:
                owner_person = parse_user(u)
        batch.sources.append({"url": html_url, "source_type": "github", "title": full,
                              "snippet": repo.description,
                              "content_hash": content_hash((repo.description or "") + (repo.readme_excerpt or ""))})
        observed: list[tuple[str, int]] = [("stars", repo.stars), ("forks", repo.forks), ("watchers", repo.watchers)]
        if open_prs is not None:  # without the PR count, open_issues_count would mix issues and PRs
            observed += [("open_issues", repo.open_issues), ("open_prs", open_prs)]
        for metric, value in observed:
            batch.metrics.append(MetricPoint(entity=repo.ref, metric=metric, day=today, value=float(value)))
        batch.signals.extend(build_creation_signals(repo, company, today))
        related_owner = [company.ref] if company else []

        if deep:
            young = company is not None and _is_young(repo, company, today)
            self._deep(batch, repo, owner_person, related_owner, today, max_contributors, star_pages, young=young)
        elif owner_person:
            batch.people.append(owner_person)
            batch.relations.append(Relation(kind="contributor", src=owner_person.ref, dst=repo.ref,
                                            attrs={"owner": True}, source_url=html_url))
        batch.repositories.append(repo)
        return batch

    def _deep(self, batch: ProviderBatch, repo: RepoRecord, owner_person: PersonRecord | None,
              related_owner: list[EntityRef], today: date, max_contributors: int,
              star_pages: int | None = None, *, young: bool = False) -> None:
        full = repo.full_name
        html_url = repo.attrs["html_url"]
        # Prime both stats computations first; GitHub computes them in the background meanwhile.
        primed_commits = self._get(f"/repos/{full}/stats/commit_activity")
        primed_contrib = self._get(f"/repos/{full}/stats/contributors")

        # Contributors (top by commits) + profiles.
        n = min(max_contributors, self.max_contributors_cap)
        r = self._get(f"/repos/{full}/contributors", params={"per_page": n})
        contributors = r.json() if r.status_code == 200 and r.content else []
        if r.status_code >= 400:
            batch.errors.append(f"{full}: contributors HTTP {r.status_code}")
        humans = [c for c in contributors if not is_bot(c.get("login"), c.get("type"))]
        if len(contributors) >= n:
            rc = self._get(f"/repos/{full}/contributors", params={"per_page": 1})
            repo.contributors_count = _last_page_from_link(rc.headers.get("link")) or len(contributors)
        else:
            repo.contributors_count = len(contributors)
        relations: dict[str, Relation] = {}
        people: dict[str, PersonRecord] = {}
        for c in humans:
            login = c["login"]
            if self._budget_ok(1) and (u := self._user(login)):
                people[login.lower()] = parse_user(u)
            relations[login.lower()] = Relation(kind="contributor", src=gh_user_ref(login), dst=repo.ref,
                                                attrs={"contributions": c.get("contributions")},
                                                source_url=f"{html_url}/graphs/contributors")
        if owner_person:
            key = owner_person.github_handle.lower() if owner_person.github_handle else ""
            people.setdefault(key, owner_person)
            rel = relations.setdefault(key, Relation(kind="contributor", src=owner_person.ref, dst=repo.ref,
                                                     source_url=html_url))
            rel.attrs["owner"] = True

        # Star history (exact daily counts).
        history = self._star_history(full, repo.stars, batch.errors, star_pages)
        if history:
            repo.attrs["star_history"] = "complete" if history.complete else "recent"
            for d, v in compact_series([p for p in history.points if p[0] < today]):
                batch.metrics.append(MetricPoint(entity=repo.ref, metric="stars", day=d, value=v, source="backfill"))
            batch.signals.extend(build_velocity_signals(repo, history, today, related=related_owner))

        # Weekly commits.
        weekly = self._stats(f"/repos/{full}/stats/commit_activity", primed_commits, batch.errors,
                             f"{full} commit_activity")
        if weekly:
            self._commit_activity(batch, repo, weekly, related_owner)
        else:
            # Fallback (signal only): rolling-week totals, not aligned to commit_activity's Sunday weeks.
            part = self._get(f"/repos/{full}/stats/participation")
            counts = (part.json() or {}).get("all") if part.status_code == 200 and part.content else None
            sig = build_commit_acceleration_signal(repo, counts or [], basis="stats/participation",
                                                   related=related_owner)
            if sig:
                batch.signals.append(sig)

        # Contributor first/last-commit weeks.
        cstats = self._stats(f"/repos/{full}/stats/contributors", primed_contrib, batch.errors,
                             f"{full} stats/contributors")
        if cstats:
            self._contributor_stats(batch, repo, cstats, relations, people, today)
        elif cstats is None and young:
            # Stats unavailable for a young org's repo: newest commit per top contributor (exact),
            # and the repository's creation date only as a labelled approximation of the first commit.
            self._probe_last_commits(repo, [relations[c["login"].lower()] for c in humans
                                            if c["login"].lower() in relations and c["login"].lower() in people])
        if repo.contributors_count is not None:
            # /contributors count (includes bots, Link-header estimate): a different measure, kept separate.
            batch.metrics.append(MetricPoint(entity=repo.ref, metric="contributors_total", day=today,
                                             value=float(repo.contributors_count)))

        # Releases.
        rr = self._get(f"/repos/{full}/releases", params={"per_page": 20})
        if rr.status_code == 200:
            releases = rr.json() or []
            batch.signals.extend(build_release_signals(repo, releases, today))
            complete = _link_rel(rr.headers.get("link"), "next") is None
            total = len(releases) if complete else self._count_via_link(f"/repos/{full}/releases", {})
            if total is not None:
                total -= sum(1 for r in releases if r.get("draft"))  # drafts are not public releases
                repo.attrs["releases_total"] = total
                batch.metrics.append(MetricPoint(entity=repo.ref, metric="releases_total", day=today,
                                                 value=float(total)))
                batch.metrics.extend(release_points(releases, total, repo.ref, today, complete=complete))

        batch.people.extend(people.values())
        # A contributor edge is only emitted when its person record is in the batch.
        batch.relations.extend(r for k, r in relations.items() if k in people)

    def _commit_activity(self, batch: ProviderBatch, repo: RepoRecord, weekly: list,
                         related: Sequence[EntityRef]) -> NormalizedSignal | None:
        """commits_week backfill + commit_acceleration signal from /stats/commit_activity."""
        pts = commit_activity_to_points(weekly, repo.ref)
        batch.metrics.extend(pts)
        sig = build_commit_acceleration_signal(repo, [p.value for p in sorted(pts, key=lambda p: p.day)],
                                               related=related)
        if sig:
            batch.signals.append(sig)
        return sig

    def _contributor_stats(self, batch: ProviderBatch, repo: RepoRecord, cstats: list,
                           relations: dict[str, Relation], people: dict[str, PersonRecord], today: date) -> None:
        """contributors backfill, first/last commit dates on contributor edges, new_contributors signal."""
        html_url = repo.attrs.get("html_url") or f"{WEB}/{repo.full_name}"
        spans = contributor_stats_to_spans(cstats)
        first = {lg: sp[0] for lg, sp in spans.items()}
        if len(cstats) < 100:  # stats cover only the top 100 authors; a truncated series would be wrong
            batch.metrics.extend(p for p in contributors_cumulative_points(first, repo.ref) if p.day < today)
            # Today's point uses the same definition as the backfill (distinct non-bot authors), so
            # window deltas over 'contributors' measure newcomers, not a change of definition.
            batch.metrics.append(MetricPoint(entity=repo.ref, metric="contributors", day=today,
                                             value=float(len(first))))
        for login, (f, last) in spans.items():
            if login.lower() in relations:
                relations[login.lower()].attrs.update(contributor_date_attrs(f, last))
        newcomers = new_contributors(first, today)
        for login in newcomers[:10]:
            key = login.lower()
            if key not in people and self._budget_ok(1) and (u := self._user(login)):
                people[key] = parse_user(u)
            if key not in relations:
                relations[key] = Relation(kind="contributor", src=gh_user_ref(login), dst=repo.ref,
                                          attrs=contributor_date_attrs(*spans[login]),
                                          source_url=f"{html_url}/graphs/contributors")
        if sig := build_new_contributors_signal(repo, newcomers, first, stats_truncated=len(cstats) >= 100):
            batch.signals.append(sig)

    def _probe_last_commits(self, repo: RepoRecord, rels: Sequence[Relation], *,
                            top: int = COMMITS_FALLBACK_TOP) -> None:
        """``/commits?author=<login>&per_page=1`` for the top contributors: exact newest-commit date.

        Only a contributor confirmed to have a commit also gets the repository's created_at, stored
        under ``first_commit_at_approximate`` (never ``first_commit_at``) with its basis spelled out."""
        full = repo.full_name
        ranked = sorted(rels, key=lambda r: -int(r.attrs.get("contributions") or 0))
        for rel in ranked[:top]:
            if rel.attrs.get("last_commit_at") or not self._budget_ok(1):
                continue
            login = split_login(rel.src)
            r = self._get(f"/repos/{full}/commits", params={"author": login, "per_page": 1})
            if r.status_code != 200:
                continue
            try:
                at = last_commit_from_commits(r.json() if r.content else [])
            except ValueError:
                at = None
            if at is None:
                continue
            rel.attrs["last_commit_at"] = at.isoformat()
            rel.attrs["last_commit_basis"] = LAST_COMMIT_BASIS
            if repo.gh_created_at and not rel.attrs.get("first_commit_at"):
                rel.attrs["first_commit_at_approximate"] = repo.gh_created_at.isoformat()
                rel.attrs["first_commit_basis"] = APPROX_FIRST_COMMIT_BASIS

    # ── stats retry (202 "computing" responses) ──
    def fetch_pending_stats(self, *, attempts: int = STATS_RETRY_ATTEMPTS,
                            spacing: float = STATS_RETRY_SPACING) -> ProviderBatch:
        """Re-request every stats endpoint that answered 202 earlier in this process (see ``pending_stats``)."""
        names = list(dict.fromkeys(fn for fn, _ in self.pending_stats))
        endpoints = {(fn, ep) for fn, ep in self.pending_stats}
        self.pending_stats = []
        if not names:
            return ProviderBatch(provider=self.name)
        return self.retry_stats(names, attempts=attempts, spacing=spacing, only=endpoints)

    def retry_stats(self, full_names: list[str], *, attempts: int = STATS_RETRY_ATTEMPTS,
                    spacing: float = STATS_RETRY_SPACING, max_contributors: int = 12,
                    only: set[tuple[str, str]] | None = None) -> ProviderBatch:
        """Fetch /stats/contributors + /stats/commit_activity for ``full_names``.

        All requests go out first; those still answering 202 are re-requested in rounds spaced
        ``spacing`` seconds apart, at most ``attempts`` more times. Returns commits_week and
        contributors metrics, contributor edges with first/last commit dates (plus their profiles)
        and the commit_acceleration / new_contributors signals. No repository records are emitted
        (fields already ingested are left untouched). Endpoints still computing at the end are put
        back on ``pending_stats``."""
        batch = ProviderBatch(provider=self.name)
        today = clock.today()
        todo = [(fn, ep) for fn in dict.fromkeys(full_names) for ep in STATS_ENDPOINTS
                if only is None or (fn, ep) in only]
        results: dict[tuple[str, str], list] = {}
        stopped = False
        for attempt in range(attempts + 1):
            if attempt:
                self._sleep(spacing)
            still: list[tuple[str, str]] = []
            for i, (fn, ep) in enumerate(todo):
                if not self._budget_ok(1):
                    batch.errors.append("stats retry: core rate-limit budget reached; stopping")
                    stopped = True
                    break
                try:
                    r = self._get(f"/repos/{fn}/stats/{ep}")
                except GitHubRateLimitError as e:
                    batch.errors.append(f"stats retry: {e}; stopping")
                    stopped = True
                    break
                except httpx.HTTPError as e:
                    batch.errors.append(f"{fn} stats/{ep}: {e}")
                    continue
                if r.status_code == 202:
                    still.append((fn, ep))
                elif r.status_code == 204:
                    results[(fn, ep)] = []
                elif r.status_code >= 400:
                    batch.errors.append(f"{fn} stats/{ep}: HTTP {r.status_code}; skipped")
                else:
                    try:
                        data = r.json() if r.content else []
                    except ValueError:
                        batch.errors.append(f"{fn} stats/{ep}: response too large to parse; skipped")
                        continue
                    results[(fn, ep)] = data if isinstance(data, list) else []
            if stopped:
                still += todo[i:]
            todo = still
            if not todo or stopped:
                break
        for fn, ep in todo:
            if not stopped:
                batch.errors.append(f"{fn} stats/{ep}: still being computed after {attempts} retries (202)")
            if (fn, ep) not in self.pending_stats:
                self.pending_stats.append((fn, ep))

        for fn in dict.fromkeys(full_names):
            repo = RepoRecord(ref=repo_ref(fn), full_name=fn, owner=fn.split("/")[0],
                              attrs={"html_url": f"{WEB}/{fn}"})
            if weekly := results.get((fn, "commit_activity")):
                self._commit_activity(batch, repo, weekly, [])
            cstats = results.get((fn, "contributors"))
            if not cstats:
                continue
            relations: dict[str, Relation] = {}
            people: dict[str, PersonRecord] = {}
            ranked = sorted((c for c in cstats if not is_bot((c.get("author") or {}).get("login"),
                                                                (c.get("author") or {}).get("type"))),
                            key=lambda c: -int(c.get("total") or 0))
            for c in ranked[:max_contributors]:
                login = c["author"]["login"]
                if self._budget_ok(1) and (u := self._user(login)):
                    people[login.lower()] = parse_user(u)
                relations[login.lower()] = Relation(kind="contributor", src=gh_user_ref(login), dst=repo.ref,
                                                    attrs={"contributions": int(c.get("total") or 0)},
                                                    source_url=f"{WEB}/{fn}/graphs/contributors")
            self._contributor_stats(batch, repo, cstats, relations, people, today)
            batch.people.extend(people.values())
            batch.relations.extend(r for k, r in relations.items() if k in people)
        return batch

    def fetch_org(self, org: str, *, max_repos: int = 6) -> ProviderBatch:
        """Org company record + its most relevant repos (top 3 deep, rest shallow)."""
        batch = ProviderBatch(provider=self.name)
        data = self._org(org)
        if not data:
            batch.errors.append(f"{org}: not a GitHub organization")
            return batch
        company = parse_org(data)
        repos = self._json(f"/orgs/{data['login']}/repos",
                           params={"type": "public", "sort": "pushed", "per_page": 50}) or []
        cutoff = clock.today() - timedelta(days=CREATED_WINDOW_DAYS)
        live = [r for r in repos if not r.get("fork") and not r.get("archived")]
        live.sort(key=lambda r: ((_dt(r.get("pushed_at")) or datetime.min.replace(tzinfo=UTC)).date()
                                 >= cutoff, r.get("stargazers_count", 0)), reverse=True)
        seen_company = False
        for i, r in enumerate(live[:max_repos]):
            try:
                sub = self.fetch_repo(r["full_name"], deep=i < 3)
            except GitHubRateLimitError as e:
                batch.errors.append(str(e))
                break
            except (GitHubAPIError, httpx.HTTPError) as e:
                batch.errors.append(f"{r['full_name']}: {e}")
                continue
            if seen_company:  # the org record is identical in every sub-batch
                sub.companies = [c for c in sub.companies if c.ref != company.ref]
            seen_company = seen_company or any(c.ref == company.ref for c in sub.companies)
            batch.extend(sub)
        if not seen_company:
            batch.companies.append(company)
        return batch

    def discover(self, queries: list[str], *, per_query: int = 6, max_repos: int = 60,
                 created_within_days: int = 150, deep: bool = True) -> ProviderBatch:
        """Search thesis queries, dedupe, fetch each repo. Per-repo errors never abort the run."""
        batch = ProviderBatch(provider=self.name)
        names: list[str] = []
        seen: set[str] = set()
        for q in queries:
            if len(names) >= max_repos:
                break
            try:
                found = self.search_repos(q, created_within_days=created_within_days, limit=per_query)
            except GitHubRateLimitError as e:
                batch.errors.append(f"search {q!r}: {e}")
                break
            except (GitHubAPIError, httpx.HTTPError) as e:
                batch.errors.append(f"search {q!r}: {e}")
                continue
            for fn in found:
                if fn.lower() not in seen and len(names) < max_repos:
                    seen.add(fn.lower())
                    names.append(fn)
        if deep:
            # Ask GitHub to start computing stats for every repo now, so the 202 "computing"
            # responses have usually resolved by the time each repo is fetched.
            try:
                for fn in names:
                    if not self._budget_ok(2):
                        break
                    self._get(f"/repos/{fn}/stats/commit_activity")
                    self._get(f"/repos/{fn}/stats/contributors")
            except (GitHubRateLimitError, httpx.HTTPError) as e:
                batch.errors.append(f"priming stats: {e}")
        seen_companies: set[str] = set()
        for fn in names:
            try:
                sub = self.fetch_repo(fn, deep=deep)
            except GitHubRateLimitError as e:
                batch.errors.append(f"{fn}: {e}; stopping")
                break
            except (GitHubAPIError, httpx.HTTPError, ValueError, KeyError) as e:
                batch.errors.append(f"{fn}: {e}")
                continue
            sub.companies = [c for c in sub.companies if c.ref.key not in seen_companies]
            seen_companies.update(c.ref.key for c in sub.companies)
            batch.extend(sub)
        return batch

    # ── young organizations (formation discovery) ──
    def _search_young_repos(self, terms: list[str], since: date, today: date, *, min_stars: int,
                            max_searches: int, errors: list[str]) -> list[dict[str, Any]]:
        """Repos created since ``since`` matching each term, sorted by recent update.

        A window whose total_count exceeds GitHub's 1,000-result cap is split in halves (the page
        already fetched is kept); windows under the cap read further pages while budget allows."""
        items: dict[str, dict[str, Any]] = {}
        per_term = max(1, max_searches // max(len(terms), 1))
        used = 0
        for term in terms:
            term_used = 0
            windows: list[tuple[date, date | None, int]] = [(since, None, 1)]
            while windows and term_used < per_term and used < max_searches:
                start, end, page = windows.pop(0)
                q = build_young_org_query(term, start, end, min_stars=min_stars)
                try:
                    data = self._json("/search/repositories", params={"q": q, "sort": "updated", "order": "desc",
                                                                      "per_page": 100, "page": page}, search=True)
                except GitHubRateLimitError as e:
                    errors.append(f"search {term!r}: {e}; stopping")
                    return list(items.values())
                except (GitHubAPIError, httpx.HTTPError) as e:
                    errors.append(f"search {term!r}: {e}")
                    break
                used += 1
                term_used += 1
                data = data or {}
                for it in data.get("items") or []:
                    items.setdefault(str(it.get("full_name", "")).lower(), it)
                total = int(data.get("total_count") or 0)
                span_end = end or today
                if page == 1 and total > SEARCH_RESULT_CAP and (span_end - start).days >= 2:
                    mid = start + (span_end - start) // 2
                    windows += [(mid + timedelta(days=1), span_end, 1), (start, mid, 1)]
                elif total > page * 100 and page < SEARCH_RESULT_CAP // 100 and len(data.get("items") or []) == 100:
                    windows.insert(0, (start, end, page + 1))
        return list(items.values())

    def discover_young_orgs(
        self, terms: list[str], *, created_within_days: int = 150, min_stars: int = 3, max_orgs: int = 40,
        min_contributors: int = 2, min_commits: int = 3, max_searches: int = YOUNG_ORG_SEARCH_BUDGET,
    ) -> ProviderBatch:
        """Young GitHub organizations with a real working team, found through thesis terms.

        1. search/repositories for recently created, starred, non-fork repos per term (≈20 searches);
           keep Organization-owned repos, dropping awesome/reading lists.
        2. Screen each org (≤6 core calls): /orgs/{o} must be created inside the window, or else its
           first public repository must be; academic (.edu-style blog), course/hackathon/list and bot
           orgs are dropped; the top repos' /contributors must show ≥ ``min_contributors`` non-bot
           people with ≥ ``min_commits`` commits each.
        3. Survivors get a deep fetch of the top repo (star history capped for young repos), shallow
           fetches of the other matches, self-reported member profiles and a ``young_org_team`` signal
           (plus the existing ``org_created``/``repo_created`` signals).
        Rejections are counted in ``batch.rejected``; reasons are kept in ``self.young_org_report``."""
        batch = ProviderBatch(provider=self.name)
        today = clock.today()
        since = today - timedelta(days=created_within_days)
        report: dict[str, list[str]] = {}
        self.young_org_report = report

        def reject(reason: str, login: str) -> None:
            report.setdefault(reason, []).append(login)
            batch.rejected += 1

        found = self._search_young_repos(terms, since, today, min_stars=min_stars, max_searches=max_searches,
                                         errors=batch.errors)
        by_org: dict[str, list[dict[str, Any]]] = {}
        logins: dict[str, str] = {}
        for it in found:
            owner = it.get("owner") or {}
            if owner.get("type") != "Organization" or it.get("fork") or it.get("archived"):
                continue
            if is_list_repo(it):
                continue
            login = owner.get("login")
            if not login:
                continue
            by_org.setdefault(login.lower(), []).append(it)
            logins[login.lower()] = login
        ranked = sorted(by_org, key=lambda k: -sum(int(r.get("stargazers_count") or 0) for r in by_org[k]))

        candidates: list[tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], int, str, date]] = []
        screened = 0
        for key in ranked:
            if screened >= max_orgs:
                break
            login = logins[key]
            if reason := noise_org_reason(login):
                reject(reason, login)
                continue
            if not self._budget_ok(YOUNG_ORG_SCREEN_CALLS):
                batch.errors.append("young orgs: core rate-limit budget reached; stopping screening")
                break
            screened += 1
            try:
                screened_org = self._screen_young_org(login, by_org[key], since, today, min_contributors=min_contributors,
                                                      min_commits=min_commits, reject=reject)
            except GitHubRateLimitError as e:
                batch.errors.append(f"{login}: {e}; stopping")
                break
            except (GitHubAPIError, httpx.HTTPError, ValueError, KeyError) as e:
                batch.errors.append(f"{login}: {e}")
                continue
            if screened_org is not None:
                candidates.append(screened_org)

        for org, repos, members, age, basis, created_on in candidates:
            try:
                self._enrich_young_org(batch, org, repos, members, age_days=age, age_basis=basis,
                                       created_on=created_on, min_commits=min_commits,
                                       window_days=created_within_days)
            except GitHubRateLimitError as e:
                batch.errors.append(f"{org['login']}: {e}; stopping")
                break
            except (GitHubAPIError, httpx.HTTPError, ValueError, KeyError) as e:
                batch.errors.append(f"{org['login']}: {e}")
        return batch

    def _screen_young_org(self, login: str, repos: list[dict[str, Any]], since: date, today: date, *,
                          min_contributors: int, min_commits: int, reject: Callable[[str, str], None]):
        org = self._org(login)
        if not org:
            reject("not_an_org", login)
            return None
        if reason := noise_org_reason(org["login"], org.get("name"), org.get("description"), org.get("blog")):
            reject(reason, org["login"])
            return None
        created = _dt(org.get("created_at"))
        if created is not None and created.date() >= since:
            created_on, basis = created.date(), "org_created_at"
        else:
            # An older (renamed / repurposed) org still qualifies if its public history starts in the window.
            first = self._json(f"/orgs/{org['login']}/repos", params={"type": "public", "sort": "created",
                                                                      "direction": "asc", "per_page": 1}) or []
            first_at = _dt(first[0].get("created_at")) if first else None
            if first_at is None or first_at.date() < since:
                reject("org_too_old", org["login"])
                return None
            created_on, basis = first_at.date(), "first_public_repo_created_at"
        top = sorted(repos, key=lambda r: -int(r.get("stargazers_count") or 0))[:YOUNG_ORG_TOP_REPOS]
        contributors: dict[str, list[dict[str, Any]]] = {}
        for r in top:
            resp = self._get(f"/repos/{r['full_name']}/contributors", params={"per_page": 30})
            contributors[r["full_name"]] = resp.json() if resp.status_code == 200 and resp.content else []
        members = qualifying_members(contributors, min_commits=min_commits)
        if len(members) < min_contributors:
            reject("too_few_contributors", org["login"])
            return None
        return org, top, members, (today - created_on).days, basis, created_on

    def _enrich_young_org(self, batch: ProviderBatch, org: dict[str, Any], repos: list[dict[str, Any]],
                          members: dict[str, dict[str, Any]], *, age_days: int, age_basis: str, created_on: date,
                          min_commits: int, window_days: int) -> None:
        company = parse_org(org)
        company.attrs["young_org_basis"] = age_basis
        sub = ProviderBatch(provider=self.name)
        for i, r in enumerate(repos):
            part = self.fetch_repo(r["full_name"], deep=i == 0, star_pages=YOUNG_ORG_STAR_PAGES)
            sub.extend(part)
        if not any(c.ref == company.ref for c in sub.companies):
            sub.companies.append(company)
        # The org record is identical in every per-repo batch: keep one (with the screening basis).
        sub.companies = [c for c in sub.companies if c.ref != company.ref] + [company]
        have_people = {p.github_handle.lower() for p in sub.people if p.github_handle}
        have_edges = {(r.src.key, r.dst.key) for r in sub.relations if r.kind == "contributor"}
        for key, m in members.items():
            if key not in have_people and self._budget_ok(1) and (u := self._user(m["login"])):
                sub.people.append(parse_user(u))
                have_people.add(key)
            if key not in have_people:
                continue
            for full, n in m["repos"].items():
                edge = (gh_user_ref(m["login"]).key, repo_ref(full).key)
                if n > 0 and edge not in have_edges:
                    sub.relations.append(Relation(kind="contributor", src=gh_user_ref(m["login"]), dst=repo_ref(full),
                                                  attrs={"contributions": n},
                                                  source_url=f"{WEB}/{full}/graphs/contributors"))
                    have_edges.add(edge)
        # Shallow repos get no stats: probe newest commits of their top members (young org by definition).
        deep_full = repos[0]["full_name"].lower() if repos else None
        by_repo: dict[str, list[Relation]] = {}
        for rel in sub.relations:
            if rel.kind == "contributor" and rel.dst.type == "repository" and not rel.attrs.get("last_commit_at"):
                by_repo.setdefault(rel.dst.key, []).append(rel)
        records = {r.ref.key: r for r in sub.repositories}
        for key, rels in by_repo.items():
            rec = records.get(key)
            if rec is not None and rec.full_name.lower() != deep_full:
                self._probe_last_commits(rec, rels)
        sub.signals.append(build_young_org_team_signal(
            company, list(members.values()), age_days=age_days, age_basis=age_basis, created_on=created_on,
            min_commits=min_commits, window_days=window_days, repos=[r["full_name"] for r in repos],
            related_logins=[m["login"] for k, m in members.items() if k in have_people]))
        batch.extend(sub)

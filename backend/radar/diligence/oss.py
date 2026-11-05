"""Open-source health from repository artifacts.

Every metric is ``{value, source_url, note}``; a metric whose inputs were not collected is ``None`` with an
"unknown" note — never estimated. Issue metrics are labelled "time to close" (we see when an issue was closed,
not when anyone first responded).
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta
from typing import Any

from radar.diligence.types import ArtifactDraft, CompanyFacts, days_between, parse_dt, parse_version

SINGLE_MAINTAINER_SHARE = 0.60  # top contributor share of commits that makes it a HIGH risk
CONCENTRATED_SHARE = 0.40
LOW_BUS_FACTOR = 2


def _metric(value: Any, url: str | None, note: str) -> dict[str, Any]:
    return {"value": value, "source_url": url, "note": note}


def _unknown(note: str) -> dict[str, Any]:
    return {"value": None, "source_url": None, "note": f"unknown — {note}"}


def is_bot(login: str) -> bool:
    lo = (login or "").lower()
    return lo.endswith("[bot]") or lo.endswith("-bot") or lo in ("dependabot", "github-actions", "renovate")


def concentration(contributions: list[int]) -> tuple[float | None, int | None]:
    """(top-1 share of commits, bus factor = fewest contributors covering ≥ 50% of commits)."""
    counts = sorted((c for c in contributions if c > 0), reverse=True)
    total = sum(counts)
    if not total:
        return None, None
    top1 = counts[0] / total
    acc, k = 0, 0
    for c in counts:
        acc += c
        k += 1
        if acc * 2 >= total:
            break
    return top1, k


def _trend(weeks: list[int]) -> str | None:
    if len(weeks) < 12:
        return None
    prev, last = sum(weeks[-12:-6]) / 6, sum(weeks[-6:]) / 6
    if prev == 0:
        return "rising" if last > 0 else "flat"
    ratio = last / prev
    return "rising" if ratio >= 1.25 else "falling" if ratio <= 0.75 else "flat"


def primary_repo(artifacts: list[ArtifactDraft]) -> ArtifactDraft | None:
    repos = sorted([a for a in artifacts if a.kind == "repo"], key=lambda a: (-(a.data.get("stars") or 0), a.key))
    return repos[0] if repos else None


def oss_health(artifacts: list[ArtifactDraft], facts: CompanyFacts, as_of: datetime) -> dict[str, Any]:
    repo = primary_repo(artifacts)
    if repo is None:
        return {"repo": None, "available": False, "metrics": {}, "risks": [],
                "unknown": ["No public repository was found; open-source health is unknown."]}
    name = repo.data.get("full_name")
    by_key = {a.key: a for a in artifacts}
    con = by_key.get(f"contributors:{name}")
    com = by_key.get(f"commits:{name}")
    rel = by_key.get(f"releases:{name}")
    iss = by_key.get(f"issues:{name}")
    prs = by_key.get(f"prs:{name}")
    m: dict[str, dict[str, Any]] = {}
    risks: list[dict[str, Any]] = []
    unknown: list[str] = []

    # contributors / concentration
    if con is not None and con.data.get("contributors") is not None:
        people = [c for c in con.data["contributors"] if not is_bot(str(c.get("login", "")))]
        top1, bus = concentration([int(c.get("contributions") or 0) for c in people])
        top = max(people, key=lambda c: (int(c.get("contributions") or 0), str(c.get("login")))) if people else None
        capped = " (first 100 listed by GitHub)" if con.data.get("capped") else ""
        m["contributors"] = _metric(len(people), con.url, f"human contributors on the default branch{capped}")
        if top1 is not None:
            m["top_contributor_share"] = _metric(round(top1, 3), con.url,
                                                 f"{top1:.0%} of commits come from {top['login']}")
            m["bus_factor"] = _metric(bus, con.url, "fewest contributors accounting for ≥ 50% of commits")
            if top1 >= SINGLE_MAINTAINER_SHARE:
                risks.append({"id": "single_maintainer", "severity": "HIGH", "url": con.url,
                              "title": f"{top1:.0%} of commits come from one contributor ({top['login']})",
                              "detail": f"Top-contributor share ≥ {SINGLE_MAINTAINER_SHARE:.0%}."})
            elif top1 >= CONCENTRATED_SHARE or (bus is not None and bus <= LOW_BUS_FACTOR):
                risks.append({"id": "concentrated", "severity": "MEDIUM", "url": con.url,
                              "title": f"Bus factor {bus}; top contributor {top1:.0%} of commits",
                              "detail": f"Top share ≥ {CONCENTRATED_SHARE:.0%} or bus factor ≤ {LOW_BUS_FACTOR}."})
        weekly = con.data.get("weekly") or []
        if weekly:
            cutoff = (as_of - timedelta(days=90)).timestamp()
            active = sorted({w["login"] for w in weekly if not is_bot(str(w.get("login", "")))
                             and any(int(x.get("w", 0)) >= cutoff and int(x.get("c", 0)) > 0 for x in w.get("weeks", []))})
            m["active_contributors_90d"] = _metric(len(active), con.url, "authors with commits in the last 90 days")
        else:
            m["active_contributors_90d"] = _unknown("per-author weekly statistics were not available")
    else:
        unknown.append("Contributor data was not collected.")
        for k in ("contributors", "top_contributor_share", "bus_factor", "active_contributors_90d"):
            m[k] = _unknown("contributor data was not collected")

    # commit cadence
    weeks = [int(w.get("total") or 0) for w in (com.data.get("weeks") or [])] if com is not None else []
    if weeks:
        last12 = weeks[-12:]
        m["commits_12w"] = _metric(sum(last12), com.url, "commits on the default branch in the last 12 weeks")
        m["weekly_commits_median"] = _metric(statistics.median(last12), com.url, "median weekly commits, last 12 weeks")
        m["weekly_commits_mean"] = _metric(round(statistics.fmean(last12), 2), com.url, "mean weekly commits, last 12 weeks")
        m["commit_trend"] = _metric(_trend(weeks), com.url, "last 6 weeks vs the 6 before (±25%)")
        if sum(last12) == 0:
            risks.append({"id": "no_recent_commits", "severity": "HIGH", "url": com.url,
                          "title": "No commits in the last 12 weeks", "detail": "Commit activity statistics."})
    else:
        for k in ("commits_12w", "weekly_commits_median", "weekly_commits_mean", "commit_trend"):
            m[k] = _unknown("commit activity statistics were not available")

    # releases
    if rel is not None:
        rs = [r for r in rel.data.get("releases") or [] if not r.get("draft")]
        dates = sorted(d for r in rs if (d := parse_dt(r.get("published_at"))))
        m["releases_90d"] = _metric(sum(1 for d in dates if 0 <= (days_between(d, as_of) or 1e9) <= 90), rel.url,
                                    "releases published in the last 90 days")
        m["releases_365d"] = _metric(sum(1 for d in dates if 0 <= (days_between(d, as_of) or 1e9) <= 365), rel.url,
                                     "releases published in the last 365 days")
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        m["release_interval_median_days"] = (_metric(statistics.median(gaps), rel.url, "median days between releases")
                                             if gaps else _unknown("fewer than two releases"))
        stable = sorted([r for r in rs if not r.get("prerelease")], key=lambda r: str(r.get("published_at") or ""))
        latest = stable[-1] if stable else (sorted(rs, key=lambda r: str(r.get("published_at") or ""))[-1] if rs else None)
        if latest:
            v = parse_version(str(latest.get("tag") or ""))
            m["latest_release"] = _metric(latest.get("tag"), latest.get("html_url") or rel.url,
                                          f"published {str(latest.get('published_at'))[:10]}"
                                          + (" (pre-release)" if latest.get("prerelease") else ""))
            m["semver_1_0"] = _metric(bool(v and v[0] >= 1 and not v[3]), rel.url, "latest stable release ≥ 1.0")
        else:
            m["latest_release"] = _metric(None, rel.url, "no tagged releases")
            m["semver_1_0"] = _metric(False, rel.url, "no tagged releases")
        if not m["releases_365d"]["value"]:
            risks.append({"id": "no_recent_release", "severity": "MEDIUM", "url": rel.url,
                          "title": "No release in the last 365 days", "detail": "GitHub releases."})
    else:
        for k in ("releases_90d", "releases_365d", "release_interval_median_days", "latest_release", "semver_1_0"):
            m[k] = _unknown("releases were not collected")

    # issues: time to close (never "response time")
    if iss is not None and iss.data.get("issues"):
        durations = [d for i in iss.data["issues"]
                     if (d := days_between(parse_dt(i.get("created_at")), parse_dt(i.get("closed_at")))) is not None]
        m["issue_time_to_close_median_days"] = (
            _metric(round(statistics.median(durations), 1), iss.url,
                    f"median time to close of the {len(durations)} most recently closed issues")
            if durations else _unknown("no closed issues with dates"))
    else:
        m["issue_time_to_close_median_days"] = _unknown("closed issues were not collected")

    if prs is not None and prs.data.get("merged_30d") is not None:
        m["prs_merged_30d"] = _metric(prs.data.get("merged_30d"), prs.url, "pull requests merged in the last 30 days")
        m["prs_merged_90d"] = _metric(prs.data.get("merged_90d"), prs.url, "pull requests merged in the last 90 days")
    else:
        m["prs_merged_30d"] = _unknown("merged-PR counts need an authenticated GitHub search")
        m["prs_merged_90d"] = _unknown("merged-PR counts need an authenticated GitHub search")

    # repository facts
    created = parse_dt(repo.data.get("created_at"))
    age = days_between(created, as_of)
    m["license"] = _metric(repo.data.get("license_spdx"), repo.url, "SPDX id reported by GitHub")
    m["archived"] = _metric(bool(repo.data.get("archived")), repo.url, "repository archived (read-only)")
    m["stars"] = _metric(repo.data.get("stars"), repo.url, "stars at collection time")
    m["forks"] = _metric(repo.data.get("forks"), repo.url, "forks at collection time")
    m["repo_age_days"] = _metric(int(age) if age is not None else None, repo.url,
                                 f"created {created.date().isoformat()}" if created else "creation date unknown")
    if repo.data.get("archived"):
        risks.append({"id": "archived", "severity": "HIGH", "url": repo.url, "title": f"{name} is archived",
                      "detail": "No further development on this repository."})

    series = facts.metrics.get(f"contributors:{name}") or []
    if len(series) >= 2:
        first_day, first = series[0]
        last_day, last = series[-1]
        m["contributor_growth"] = _metric(last - first, repo.url,
                                          f"contributors {first:g} on {first_day} → {last:g} on {last_day} (radar history)")
    else:
        m["contributor_growth"] = _unknown("no contributor history in the radar")

    maturity = _maturity(m, age)
    risks.sort(key=lambda r: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[r["severity"]], r["id"]))
    unknown += [f"{k.replace('_', ' ')}: {v['note']}" for k, v in m.items() if v["value"] is None
                and v["note"].startswith("unknown")]
    return {"repo": name, "repo_url": repo.url, "available": True, "metrics": m, "risks": risks,
            "maturity": maturity, "unknown": unknown}


def _maturity(m: dict[str, dict[str, Any]], age: float | None) -> str:
    if m["archived"]["value"]:
        return "archived"
    stable = bool(m.get("semver_1_0", {}).get("value"))
    bus = m.get("bus_factor", {}).get("value")
    if age is not None and age >= 730 and stable and (bus or 0) >= 3:
        return "established"
    if age is not None and age < 365 and not stable:
        return "early"
    return "developing" if age is not None else "unknown"

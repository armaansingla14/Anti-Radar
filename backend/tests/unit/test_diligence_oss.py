"""Open-source health math: concentration, bus factor, cadence, releases, time to close; unknown when missing."""

from __future__ import annotations

from datetime import timedelta

import pytest

from radar.diligence.oss import concentration, oss_health
from tests.fixtures.diligence.helpers import AS_OF, REPO, art, facts, releases, repo


def test_concentration_and_bus_factor():
    top1, bus = concentration([710, 150, 80, 40, 20])
    assert top1 == pytest.approx(0.71) and bus == 1
    top1, bus = concentration([30, 30, 20, 20])
    assert top1 == pytest.approx(0.3) and bus == 2
    assert concentration([]) == (None, None)
    assert concentration([0, 0]) == (None, None)


def _contributors(counts, capped=False):
    return art(f"contributors:{REPO}", "contributors", "code", "", {
        "full_name": REPO, "capped": capped,
        "contributors": [{"login": f"dev{i}", "contributions": c} for i, c in enumerate(counts)]
        + [{"login": "dependabot[bot]", "contributions": 5000}],
    })


def test_single_maintainer_is_high_risk_and_bots_are_excluded():
    h = oss_health([repo(), _contributors([710, 150, 80, 40, 20])], facts(), AS_OF)
    assert h["metrics"]["top_contributor_share"]["value"] == pytest.approx(0.71)
    assert h["metrics"]["contributors"]["value"] == 5
    assert h["risks"][0]["id"] == "single_maintainer" and h["risks"][0]["severity"] == "HIGH"
    assert "71% of commits come from one contributor" in h["risks"][0]["title"]


def test_cadence_releases_and_time_to_close():
    weeks = [{"week": 0, "total": t} for t in [0] * 40 + [2] * 6 + [6] * 6]
    commits = art(f"commits:{REPO}", "commits", "code", "", {"full_name": REPO, "weeks": weeks})
    rel = releases([
        {"tag": "v1.1.0", "published_at": (AS_OF - timedelta(days=20)).isoformat()},
        {"tag": "v1.0.0", "published_at": (AS_OF - timedelta(days=80)).isoformat()},
        {"tag": "v0.9.0", "published_at": (AS_OF - timedelta(days=200)).isoformat()},
    ])
    issues = art(f"issues:{REPO}", "issues", "code", "", {"full_name": REPO, "issues": [
        {"created_at": (AS_OF - timedelta(days=10)).isoformat(), "closed_at": (AS_OF - timedelta(days=9)).isoformat()},
        {"created_at": (AS_OF - timedelta(days=10)).isoformat(), "closed_at": (AS_OF - timedelta(days=7)).isoformat()},
        {"created_at": (AS_OF - timedelta(days=10)).isoformat(), "closed_at": (AS_OF - timedelta(days=5)).isoformat()},
    ]})
    m = oss_health([repo(), commits, rel, issues], facts(), AS_OF)["metrics"]
    assert m["commits_12w"]["value"] == 48 and m["weekly_commits_median"]["value"] == 4.0
    assert m["commit_trend"]["value"] == "rising"
    assert m["releases_90d"]["value"] == 2 and m["releases_365d"]["value"] == 3
    assert m["release_interval_median_days"]["value"] == 90.0
    assert m["latest_release"]["value"] == "v1.1.0" and m["semver_1_0"]["value"] is True
    assert m["issue_time_to_close_median_days"]["value"] == 3.0
    assert "time to close" in m["issue_time_to_close_median_days"]["note"]
    assert "response" not in m["issue_time_to_close_median_days"]["note"]


def test_missing_inputs_are_unknown_not_zero():
    h = oss_health([repo()], facts(), AS_OF)
    m = h["metrics"]
    for k in ("contributors", "bus_factor", "commits_12w", "releases_365d", "issue_time_to_close_median_days",
              "prs_merged_30d", "contributor_growth"):
        assert m[k]["value"] is None and m[k]["note"].startswith("unknown"), k
    assert h["unknown"]
    none = oss_health([], facts(), AS_OF)
    assert none["available"] is False and none["metrics"] == {}


def test_contributor_growth_from_radar_history_and_archived_risk():
    f = facts(metrics={f"contributors:{REPO}": [["2026-07-01", 4], ["2026-10-01", 9]]})
    h = oss_health([repo(archived=True)], f, AS_OF)
    assert h["metrics"]["contributor_growth"]["value"] == 5
    assert h["maturity"] == "archived" and h["risks"][0]["id"] == "archived"

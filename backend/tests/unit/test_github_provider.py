"""GitHub provider: parsing, exact history math, signal thresholds and HTTP orchestration.

All HTTP is mocked with respx against handcrafted fixtures (fictional entities) in
tests/fixtures/github; "today" is pinned to 2026-10-06 by conftest.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import respx

from radar.providers.base import EntityRef, ProviderBatch, domain_ref, gh_org_ref, gh_user_ref, repo_ref
from radar.providers.github import (
    API,
    APPROX_FIRST_COMMIT_BASIS,
    CONTRIBUTOR_DATES_BASIS,
    LAST_COMMIT_BASIS,
    GitHubProvider,
    GitHubRateLimitError,
    StarHistory,
    build_commit_acceleration_signal,
    build_creation_signals,
    build_new_contributors_signal,
    build_release_signals,
    build_search_query,
    build_velocity_signals,
    build_young_org_query,
    commit_activity_to_points,
    compact_series,
    contributor_stats_to_first_weeks,
    contributor_stats_to_spans,
    contributors_cumulative_points,
    is_bot,
    is_list_repo,
    last_commit_from_commits,
    new_contributors,
    noise_org_reason,
    parse_org,
    parse_repo,
    parse_user,
    qualifying_members,
    readme_sha,
    release_points,
    series_deltas,
    star_history_to_series,
    strip_markup,
)
from radar.providers.http import HttpClient

FX = Path(__file__).parent.parent / "fixtures" / "github"
TODAY = date(2026, 10, 6)
REPO = "acme-robotics/graspnet"


def load(name: str):
    return json.loads((FX / name).read_text())


def star_weeks() -> list[dict]:
    return load("star_history_p1.json") + load("star_history_p2.json")


@pytest.fixture()
def sleeps() -> list[float]:
    return []


@pytest.fixture()
def provider(sleeps) -> GitHubProvider:
    p = GitHubProvider(client=HttpClient(min_interval=0, max_retries=0), token="test-token", sleep=sleeps.append)
    p.search_interval = 0
    return p


# ── parsing ──────────────────────────────────────────────────────────────────


def test_parse_repo_fields():
    r = parse_repo(load("repo_org.json"))
    assert r.ref == repo_ref(REPO)
    assert (r.stars, r.forks, r.watchers, r.open_issues) == (1000, 87, 41, 12)
    assert r.owner == "acme-robotics" and r.owner_type == "Organization"
    assert r.license == "Apache-2.0" and r.topics == ["robotics", "manipulation", "grasping"]
    assert r.gh_created_at == datetime(2026, 6, 1, 10, tzinfo=UTC)
    assert r.owner_company is None  # set by the provider only when the owner org is fetched


def test_parse_repo_noassertion_license_and_empty_homepage():
    r = parse_repo(load("repo_user.json"))
    assert r.license == "Other"
    assert r.homepage is None


def test_parse_org_with_company_domain():
    c = parse_org(load("org_acme.json"))
    assert c.ref == gh_org_ref("acme-robotics")
    assert c.kind == "company"
    assert domain_ref("acme-robotics.ai") in c.alt_refs
    assert EntityRef(type="company", key="x:acmerobotics") in c.alt_refs
    assert c.name == "Acme Robotics" and c.x_handle == "AcmeRobotics"
    assert c.founded_date == date(2026, 5, 15)
    assert c.attrs["public_repos"] == 7 and c.attrs["followers"] == 230
    assert c.attrs["gh_org_created_at"].startswith("2026-05-15")


def test_parse_org_platform_blog_has_no_domain_ref():
    c = parse_org(load("org_platform_blog.json"))
    assert c.kind == "project"
    assert not any(a.key.startswith("domain:") for a in c.alt_refs)
    assert c.website is None
    assert c.name == "grasp-lab"  # falls back to login


def test_parse_org_academic_domain_is_lab_without_domain_ref():
    data = {**load("org_platform_blog.json"), "login": "mit-grasp", "blog": "https://grasp.csail.mit.edu"}
    c = parse_org(data)
    assert c.kind == "lab"
    assert not any(a.key.startswith("domain:") for a in c.alt_refs)


def test_parse_org_platform_own_org_keeps_its_domain():
    data = {**load("org_platform_blog.json"), "login": "huggingface", "blog": "https://huggingface.co"}
    c = parse_org(data)
    assert c.kind == "company" and domain_ref("huggingface.co") in c.alt_refs


def test_parse_user_company_and_handles():
    p = parse_user(load("user_alice.json"))
    assert p.ref == gh_user_ref("alice")
    assert p.current_company == "acme-robotics mit"
    assert p.x_handle == "alice_ex" and EntityRef(type="person", key="x:alice_ex") in p.alt_refs
    assert p.personal_site is None
    b = parse_user(load("user_bob.json"))
    assert b.name == "bob" and b.current_company is None


@pytest.mark.parametrize(
    ("login", "typ", "expected"),
    [("dependabot[bot]", "Bot", True), ("renovate-bot", "User", True), ("github-actions", None, True),
     ("alice", "User", False), (None, "User", True), ("someapp", "Bot", True)],
)
def test_is_bot(login, typ, expected):
    assert is_bot(login, typ) is expected


def test_strip_markup_readme():
    text = strip_markup((FX / "readme_graspnet.md").read_text())
    assert text is not None
    assert "GraspNet plans dexterous grasps for low-cost arms. See the paper." in text
    for junk in ("<img", "badge.svg", "pip install", "```", "**", "](", "|"):
        assert junk not in text
    long = strip_markup("word " * 1000, limit=50)
    assert long is not None and len(long) <= 52 and long.endswith("…")


# ── star history math ────────────────────────────────────────────────────────


def test_star_history_partial_coverage_is_exact_and_bounded():
    weeks = [
        {"week": int(datetime(2026, 10, 4, tzinfo=UTC).timestamp()), "days": [10, 5, 3, 7, 7, 0, 0]},
        {"week": int(datetime(2026, 9, 27, tzinfo=UTC).timestamp()), "days": [2] * 7},
        {"week": int(datetime(2026, 9, 20, tzinfo=UTC).timestamp()), "days": [1] * 7},
    ]
    h = star_history_to_series(weeks, 100, complete=False, today=TODAY)  # Oct 7/8 are future: ignored
    assert h.value_at(TODAY) == 100
    assert h.value_at(date(2026, 10, 5)) == 97
    assert h.value_at(date(2026, 10, 3)) == 82
    assert h.value_at(date(2026, 9, 29)) == 74
    assert h.value_at(date(2026, 9, 19)) == 61  # coverage boundary is exact
    assert h.value_at(date(2026, 9, 18)) is None  # before coverage: unknown, not zero
    assert series_deltas(h, TODAY) is None  # needs 35 days of coverage


def test_star_history_complete_starts_at_zero():
    weeks = [{"week": int(datetime(2026, 9, 27, tzinfo=UTC).timestamp()), "days": [0, 3, 0, 2, 0, 0, 0]}]
    h = star_history_to_series(weeks, 5, complete=True, today=TODAY)
    assert h.points == [(date(2026, 9, 28), 3.0), (date(2026, 9, 30), 5.0)]
    assert h.value_at(date(2026, 9, 1)) == 0
    assert h.value_at(date(2026, 9, 29)) == 3


def test_series_deltas_exact_numbers():
    h = star_history_to_series(star_weeks(), 1000, complete=False, today=TODAY)
    d = series_deltas(h, TODAY)
    assert d == {"stars_now": 1000, "stars_7d": 70, "stars_30d": 93, "stars_30d_ago": 907,
                 "prev_28d": 28, "prev_weekly_rate": 7.0}


def test_velocity_and_acceleration_signals():
    repo = parse_repo(load("repo_org.json"))
    h = star_history_to_series(star_weeks(), 1000, complete=False, today=TODAY)
    sigs = {s.signal_type: s for s in build_velocity_signals(repo, h, TODAY)}
    v = sigs["star_velocity"]
    assert v.raw_value == 93 and v.epistemic == "FACT"
    assert v.description.startswith("+93 stars in 30 days (from 907 to 1,000)")
    assert 0 < v.normalized_value < 1
    a = sigs["star_acceleration"]
    assert a.raw_value == 70 and a.metadata["ratio"] == 10.0 and a.metadata["prev_weekly_rate"] == 7.0
    assert v.source_url == "https://github.com/acme-robotics/graspnet"


def test_no_velocity_below_threshold_and_no_acceleration_for_young_repo():
    repo = parse_repo(load("repo_org.json"))
    # Steady 0.5 stars/day: < 25 in 30 days -> nothing.
    weeks = [{"week": int(datetime(2026, 8, 2, tzinfo=UTC).timestamp()) + 7 * 86400 * i, "days": [1, 0] * 3 + [0]}
             for i in range(10)]
    h = star_history_to_series(weeks, 500, complete=False, today=TODAY)
    assert build_velocity_signals(repo, h, TODAY) == []
    # Young repo (created 20 days ago): velocity yes, acceleration no.
    young = repo.model_copy(update={"gh_created_at": datetime(2026, 9, 16, tzinfo=UTC)})
    h2 = star_history_to_series(star_weeks(), 1000, complete=False, today=TODAY)
    assert [s.signal_type for s in build_velocity_signals(young, h2, TODAY)] == ["star_velocity"]


def test_compact_series_caps_and_keeps_recent():
    start = date(2023, 1, 1)
    pts = [(start + timedelta(days=i), float(i)) for i in range(1300)]
    out = compact_series(pts, max_points=400, keep_recent_days=90)
    assert len(out) == 400
    assert out[0] == pts[0] and out[-90:] == pts[-90:]
    assert [p[0] for p in out] == sorted(p[0] for p in out)


def test_star_history_monotone_value_lookup():
    h = StarHistory(points=[(date(2026, 1, 1), 5.0), (date(2026, 2, 1), 9.0)], complete=True)
    assert h.value_at(date(2026, 1, 15)) == 5.0  # step function, no interpolation


# ── commits / contributors ───────────────────────────────────────────────────


def test_commit_activity_points_and_acceleration():
    ref = repo_ref(REPO)
    pts = commit_activity_to_points(load("commit_activity.json"), ref)
    assert len(pts) == 52 and pts[-1].day == date(2026, 10, 4) and pts[-1].value == 8
    assert all(p.source == "backfill" and p.metric == "commits_week" for p in pts)
    repo = parse_repo(load("repo_org.json"))
    sig = build_commit_acceleration_signal(repo, [p.value for p in pts])
    assert sig is not None
    assert sig.metadata["commits_4w"] == 45 and sig.metadata["commits_prev_4w"] == 20
    assert sig.metadata["ratio"] == 2.25
    # 1.8x not exceeded -> nothing; tiny volume -> nothing.
    assert build_commit_acceleration_signal(repo, [10] * 4 + [17] * 4) is None
    assert build_commit_acceleration_signal(repo, [1] * 4 + [4] * 4) is None


def test_contributor_first_weeks_and_new_contributors():
    first = contributor_stats_to_first_weeks(load("stats_contributors.json"))
    assert first == {"alice": date(2026, 3, 1), "dave": date(2026, 8, 23), "bob": date(2026, 9, 13),
                     "carol": date(2026, 9, 20)}
    assert new_contributors(first, TODAY) == ["bob", "carol"]
    cum = contributors_cumulative_points(first, repo_ref(REPO))
    assert [(p.day, p.value) for p in cum] == [(date(2026, 3, 1), 1), (date(2026, 8, 23), 2),
                                               (date(2026, 9, 13), 3), (date(2026, 9, 20), 4)]
    # Founding cohort of a brand-new repo is not "new contributors".
    assert new_contributors({"a": date(2026, 9, 27), "b": date(2026, 9, 27)}, TODAY) == []
    repo = parse_repo(load("repo_org.json"))
    sig = build_new_contributors_signal(repo, ["bob", "carol"], first)
    assert sig is not None and sig.metadata["logins"] == ["bob", "carol"]
    assert sig.related == [gh_user_ref("bob"), gh_user_ref("carol")]
    assert build_new_contributors_signal(repo, ["bob"], first) is None


def test_release_and_creation_signals():
    repo = parse_repo(load("repo_org.json"))
    rel = build_release_signals(repo, load("releases.json"), TODAY)
    assert [s.title for s in rel] == ["acme-robotics/graspnet: Released v0.2.0"]
    assert rel[0].event_at == datetime(2026, 9, 15, 12, tzinfo=UTC)
    assert rel[0].metadata["first_release"] is False
    company = parse_org(load("org_acme.json"))
    created = {s.signal_type: s for s in build_creation_signals(repo, company, TODAY)}
    assert created["repo_created"].event_at == repo.gh_created_at
    assert created["repo_created"].related == [company.ref]
    assert created["org_created"].entity == company.ref
    old = repo.model_copy(update={"gh_created_at": datetime(2024, 1, 1, tzinfo=UTC)})
    assert build_creation_signals(old, None, TODAY) == []


def test_build_search_query():
    q = build_search_query("robot manipulation", created_after=date(2026, 5, 9), min_stars=15)
    assert q == ('"robot manipulation" in:name,description,topics,readme created:>2026-05-09 stars:>=15 '
                 "fork:false archived:false")


# ── HTTP orchestration (respx) ───────────────────────────────────────────────


def _mock_org_repo(router: respx.Router, *, commit_statuses: list[int], contrib_statuses: list[int] | None = None,
                   ) -> None:
    base = f"{API}/repos/{REPO}"
    router.get(base).respond(json=load("repo_org.json"))
    router.get(f"{base}/readme").respond(text=(FX / "readme_graspnet.md").read_text())
    router.get(f"{API}/orgs/acme-robotics").respond(json=load("org_acme.json"))
    ca = [httpx.Response(s, json=load("commit_activity.json") if s == 200 else {}) for s in commit_statuses]
    router.get(f"{base}/stats/commit_activity").mock(side_effect=ca)
    router.get(f"{base}/stats/participation").respond(202, json={})
    if contrib_statuses is None:
        router.get(f"{base}/stats/contributors").respond(json=load("stats_contributors.json"))
    else:
        router.get(f"{base}/stats/contributors").mock(side_effect=[
            httpx.Response(s, json=load("stats_contributors.json") if s == 200 else {}) for s in contrib_statuses])
    router.get(f"{base}/contributors").respond(json=load("contributors.json"))
    for u in ("alice", "bob", "carol"):
        router.get(f"{API}/users/{u}").respond(json=load(f"user_{u}.json"))
    page2 = f"{API}/repositories/900001/stargazers/history?page=2"
    router.get(f"{base}/stargazers/history").respond(
        json=load("star_history_p1.json"), headers={"link": f'<{page2}>; rel="next", <{page2}>; rel="last"'})
    router.get(page2).respond(json=load("star_history_p2.json"))
    router.get(f"{base}/releases").respond(json=load("releases.json"))
    pulls = f"{base}/pulls"
    router.get(pulls).respond(json=[{"number": 7}],
                              headers={"link": f'<{pulls}?state=open&per_page=1&page=3>; rel="last"'})


def test_fetch_repo_org_deep_end_to_end(provider, sleeps):
    with respx.mock(assert_all_called=False) as router:
        # Stats still computing twice, then ready.
        _mock_org_repo(router, commit_statuses=[202, 202, 200])
        batch = provider.fetch_repo(REPO, deep=True)
    assert batch.errors == []
    assert sleeps == [2.0, 4.0]  # primed request + two backoff retries
    repo = batch.repositories[0]
    assert repo.owner_company == gh_org_ref("acme-robotics")
    assert repo.readme_excerpt and "dexterous grasps" in repo.readme_excerpt
    assert repo.contributors_count == 5 and repo.attrs["star_history"] == "complete"
    assert [c.kind for c in batch.companies] == ["company"]
    # Bots filtered; every contributor edge has a person record.
    logins = sorted(p.github_handle for p in batch.people)
    assert logins == ["alice", "bob", "carol"]
    contrib = {r.src.key: r for r in batch.relations if r.kind == "contributor"}
    assert set(contrib) == {"github_user:alice", "github_user:bob", "github_user:carol"}
    assert contrib["github_user:bob"].attrs == {
        "contributions": 22, "first_commit_week": "2026-09-13", "first_commit_at": "2026-09-13T00:00:00+00:00",
        "last_commit_at": "2026-09-20T00:00:00+00:00", "commit_dates_basis": CONTRIBUTOR_DATES_BASIS}
    assert contrib["github_user:alice"].attrs["first_commit_at"] == "2026-03-01T00:00:00+00:00"
    assert contrib["github_user:alice"].attrs["last_commit_at"] == "2026-09-27T00:00:00+00:00"
    assert provider.pending_stats == []
    assert any(r.kind == "owns" and r.src == gh_org_ref("acme-robotics") for r in batch.relations)
    # Observed metrics for today, backfill strictly before today.
    observed = {(m.metric, m.value) for m in batch.metrics if m.source == "observed"}
    # 'contributors' today uses the backfill's definition (distinct non-bot authors from /stats/contributors);
    # the /contributors count (bots included) is a separate metric so deltas never mix the two.
    # open_issues_count (12) includes the 3 open PRs counted from the pulls Link header.
    assert observed == {("stars", 1000), ("forks", 87), ("open_issues", 9), ("open_prs", 3), ("watchers", 41),
                        ("contributors", 4), ("contributors_total", 5), ("releases_total", 2)}
    assert repo.open_issues == 9 and repo.attrs["open_prs"] == 3
    raw = (FX / "readme_graspnet.md").read_text()
    assert repo.attrs["readme_sha"] == readme_sha(raw) and len(repo.attrs["readme_sha"]) == 64
    rel_bf = sorted((m.day, m.value) for m in batch.metrics if m.metric == "releases_total" and m.source == "backfill")
    assert rel_bf == [(date(2026, 6, 19), 0), (date(2026, 6, 20), 1), (date(2026, 9, 15), 2)]
    contrib_bf = [m for m in batch.metrics if m.metric == "contributors" and m.source == "backfill"]
    assert contrib_bf and max(contrib_bf, key=lambda m: m.day).value <= 4
    star_bf = [m for m in batch.metrics if m.metric == "stars" and m.source == "backfill"]
    assert star_bf and max(m.day for m in star_bf) == date(2026, 10, 5)
    assert next(m for m in star_bf if m.day == date(2026, 9, 29)).value == 930
    assert all(m.day < TODAY for m in batch.metrics if m.source == "backfill" and m.metric != "commits_week")
    types = sorted(s.signal_type for s in batch.signals)
    assert types == ["commit_acceleration", "new_contributors", "org_created", "release_published",
                     "repo_created", "star_acceleration", "star_velocity"]
    assert all(s.source_type == "github" and s.source_url for s in batch.signals)


def test_stats_202_exhausted_is_recorded_not_raised(provider, sleeps):
    with respx.mock(assert_all_called=False) as router:
        _mock_org_repo(router, commit_statuses=[202] * 4)
        batch = provider.fetch_repo(REPO, deep=True)
    assert sleeps == [2.0, 4.0, 8.0]
    assert any("commit_activity" in e and "202" in e for e in batch.errors)
    assert not any(m.metric == "commits_week" for m in batch.metrics)
    assert "commit_acceleration" not in {s.signal_type for s in batch.signals}
    assert "star_velocity" in {s.signal_type for s in batch.signals}  # rest of the batch intact
    assert provider.pending_stats == [(REPO, "commit_activity")]


def test_contributor_spans_and_last_commit_parsing():
    spans = contributor_stats_to_spans(load("stats_contributors.json"))
    assert spans == {"alice": (date(2026, 3, 1), date(2026, 9, 27)), "dave": (date(2026, 8, 23), date(2026, 8, 23)),
                     "bob": (date(2026, 9, 13), date(2026, 9, 20)), "carol": (date(2026, 9, 20), date(2026, 9, 20))}
    assert last_commit_from_commits(load("commits_by_author.json")) == datetime(2026, 10, 2, 17, 45, tzinfo=UTC)
    committer_only = [{"commit": {"committer": {"date": "2026-10-01T00:00:00Z"}}}]
    assert last_commit_from_commits(committer_only) == datetime(2026, 10, 1, tzinfo=UTC)
    assert last_commit_from_commits([]) is None and last_commit_from_commits({"message": "x"}) is None


def test_pending_stats_are_filled_later_in_the_same_process(provider, sleeps):
    with respx.mock(assert_all_called=False) as router:
        # 202 on the primed call + 3 backoff retries, then (on the later retry) 202 once more, then ready.
        _mock_org_repo(router, commit_statuses=[202] * 4 + [202, 200])
        provider.fetch_repo(REPO, deep=True)
        assert provider.pending_stats == [(REPO, "commit_activity")]
        sleeps.clear()
        later = provider.fetch_pending_stats()
        calls = [str(c.request.url) for c in router.calls]
    assert sleeps == [10.0]
    assert later.errors == [] and provider.pending_stats == []
    assert later.repositories == []  # never overwrites already-ingested repository fields
    weeks = [m for m in later.metrics if m.metric == "commits_week"]
    assert weeks and all(m.entity == repo_ref(REPO) and m.source == "backfill" for m in weeks)
    assert [s.signal_type for s in later.signals] == ["commit_acceleration"]
    # Only the endpoint that was pending is re-requested.
    assert not any(u.endswith("/stats/contributors") for u in calls[-2:])
    assert provider.fetch_pending_stats().counts() == ProviderBatch(provider="github").counts()


def test_retry_stats_fills_contributor_dates_and_gives_up_after_four_retries(provider, sleeps):
    base = f"{API}/repos/{REPO}"
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{base}/stats/contributors").mock(side_effect=[
            httpx.Response(202, json={}), httpx.Response(200, json=load("stats_contributors.json"))])
        ca = router.get(f"{base}/stats/commit_activity").respond(202, json={})
        for u in ("alice", "bob", "carol"):
            router.get(f"{API}/users/{u}").respond(json=load(f"user_{u}.json"))
        router.get(f"{API}/users/dave").respond(404, json={"message": "Not Found"})
        batch = provider.retry_stats([REPO, REPO])
    assert sleeps == [10.0] * 4 and ca.call_count == 5
    assert batch.errors == [f"{REPO} stats/commit_activity: still being computed after 4 retries (202)"]
    assert provider.pending_stats == [(REPO, "commit_activity")]
    edges = {r.src.key: r.attrs for r in batch.relations}
    assert set(edges) == {"github_user:alice", "github_user:bob", "github_user:carol"}  # dave has no profile
    assert edges["github_user:alice"]["first_commit_at"] == "2026-03-01T00:00:00+00:00"
    assert edges["github_user:alice"]["last_commit_at"] == "2026-09-27T00:00:00+00:00"
    assert edges["github_user:alice"]["contributions"] == 2  # stats "total"
    assert {m.metric for m in batch.metrics} == {"contributors"}
    assert [s.signal_type for s in batch.signals] == ["new_contributors"]


def test_young_org_repo_without_stats_probes_newest_commits(provider, sleeps):
    base = f"{API}/repos/{REPO}"
    with respx.mock(assert_all_called=False) as router:
        _mock_org_repo(router, commit_statuses=[200], contrib_statuses=[202] * 4)
        commits = router.get(f"{base}/commits").mock(side_effect=lambda req: httpx.Response(
            200, json=load("commits_by_author.json") if req.url.params["author"] == "alice" else []))
        batch = provider.fetch_repo(REPO, deep=True)
    assert provider.pending_stats == [(REPO, "contributors")]
    assert sorted(c.request.url.params["author"] for c in commits.calls) == ["alice", "bob", "carol"]
    assert all(c.request.url.params["per_page"] == "1" for c in commits.calls)
    edges = {r.src.key: r.attrs for r in batch.relations if r.kind == "contributor"}
    alice = edges["github_user:alice"]
    assert alice["last_commit_at"] == "2026-10-02T17:45:00+00:00" and alice["last_commit_basis"] == LAST_COMMIT_BASIS
    # The repo's creation date is only an approximation, never written as first_commit_at.
    assert "first_commit_at" not in alice
    assert alice["first_commit_at_approximate"] == "2026-06-01T10:00:00+00:00"
    assert alice["first_commit_basis"] == APPROX_FIRST_COMMIT_BASIS
    assert edges["github_user:bob"] == {"contributions": 22}  # no commit found: nothing invented


def test_old_org_repo_without_stats_is_not_probed(provider):
    old_org = {**load("org_acme.json"), "created_at": "2019-01-01T00:00:00Z"}
    old_repo = {**load("repo_org.json"), "created_at": "2020-01-01T00:00:00Z"}
    with respx.mock(assert_all_called=False) as router:
        _mock_org_repo(router, commit_statuses=[200], contrib_statuses=[202] * 4)
        router.get(f"{API}/orgs/acme-robotics").respond(json=old_org)
        router.get(f"{API}/repos/{REPO}").respond(json=old_repo)
        commits = router.get(f"{API}/repos/{REPO}/commits").respond(json=load("commits_by_author.json"))
        provider.fetch_repo(REPO, deep=True)
    assert not commits.called


def test_user_owned_repo_is_not_a_company(provider):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/repos/jdoe/tinyvla").respond(json=load("repo_user.json"))
        router.get(f"{API}/repos/jdoe/tinyvla/readme").respond(404, json={"message": "Not Found"})
        router.get(f"{API}/users/jdoe").respond(json=load("user_jdoe.json"))
        router.get(f"{API}/repos/jdoe/tinyvla/pulls").respond(json=[])
        orgs = router.get(url__regex=rf"{API}/orgs/.*").respond(404)
        batch = provider.fetch_repo("jdoe/tinyvla", deep=False)
    assert not orgs.called
    assert batch.companies == []
    repo = batch.repositories[0]
    assert repo.owner_company is None and repo.readme_excerpt is None
    assert [p.ref for p in batch.people] == [gh_user_ref("jdoe")]
    assert batch.people[0].current_company == "acme-robotics"
    (rel,) = batch.relations
    assert rel.kind == "contributor" and rel.attrs == {"owner": True} and rel.dst == repo_ref("jdoe/tinyvla")
    assert [s.signal_type for s in batch.signals] == []  # created 2024: no repo_created


def test_search_repos_builds_query(provider):
    with respx.mock() as router:
        route = router.get(f"{API}/search/repositories").respond(
            json={"items": [{"full_name": "a/x"}, {"full_name": "b/y"}, {"full_name": "c/z"}]})
        names = provider.search_repos("robot manipulation", limit=2)
    assert names == ["a/x", "b/y"]
    params = route.calls.last.request.url.params
    assert "created:>2026-05-09" in params["q"] and "stars:>=15" in params["q"]
    assert params["sort"] == "stars"
    assert route.calls.last.request.headers["authorization"] == "Bearer test-token"
    assert route.calls.last.request.headers["x-github-api-version"] == "2022-11-28"


def test_discover_collects_errors_and_stops_on_rate_limit(provider):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/search/repositories").respond(
            json={"items": [{"full_name": "a/x"}, {"full_name": "b/y"}, {"full_name": "c/z"}]})
        router.get(f"{API}/repos/a/x").respond(404, json={"message": "Not Found"})
        router.get(f"{API}/repos/b/y").respond(
            403, json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})
        never = router.get(f"{API}/repos/c/z").respond(json={})
        batch = provider.discover(["q1", "q1 again"], per_query=3, deep=False)
    assert len(batch.errors) == 2
    assert "404" in batch.errors[0] and "rate limit" in batch.errors[1]
    assert not never.called
    assert batch.repositories == []


def test_rate_limit_error_raised_directly(provider):
    with respx.mock() as router:
        router.get(f"{API}/repos/b/y").respond(403, headers={"x-ratelimit-remaining": "0"})
        with pytest.raises(GitHubRateLimitError):
            provider.fetch_repo("b/y")


def test_enabled_reason_mentions_token():
    anon = GitHubProvider(client=HttpClient(), token="")
    ok, reason = anon.enabled()
    assert ok and "GITHUB_TOKEN" in reason



def test_parse_user_marks_profile_fields_self_reported():
    p = parse_user(load("user_alice.json"))
    assert p.attrs["self_reported"] is True
    assert "company" in p.attrs["self_reported_fields"]


def test_release_points_are_anchored_on_total_and_exact_at_boundary():
    rels = [{"published_at": "2026-09-15T12:00:00Z"}, {"published_at": "2026-09-15T18:00:00Z"},
            {"published_at": "2026-08-01T00:00:00Z"}, {"draft": True, "published_at": None},
            {"published_at": "2026-10-06T09:00:00Z"}]  # today: only the observed point covers it
    pts = release_points(rels, 10, repo_ref(REPO), TODAY, complete=False)
    assert [(p.day, p.value) for p in pts] == [(date(2026, 7, 31), 6), (date(2026, 8, 1), 7), (date(2026, 9, 15), 9)]
    assert all(p.source == "backfill" and p.metric == "releases_total" for p in pts)
    assert release_points([], 3, repo_ref(REPO), TODAY, complete=True) == []


# ── young-organization discovery ─────────────────────────────────────────────


def test_build_young_org_query():
    assert build_young_org_query("robot learning", date(2026, 5, 9)) == (
        '"robot learning" created:>=2026-05-09 stars:>=3 fork:false')
    assert build_young_org_query("vla", date(2026, 5, 9), date(2026, 7, 1), min_stars=5) == (
        "vla created:2026-05-09..2026-07-01 stars:>=5 fork:false")


@pytest.mark.parametrize(
    ("login", "name", "desc", "blog", "reason"),
    [("newbot-ai", "NewBot", "Robot hands", "https://newbot.example", None),
     ("xyz-grasp", None, None, "https://xyz.stanford.edu", "academic"),
     ("deploy-bot", None, None, None, "bot"),
     ("awesome-vla", None, None, None, "list"),
     ("cs287-course-2026", None, None, None, "course_or_event"),
     ("RoboHackathon", None, None, None, "course_or_event"),
     ("acme", "Acme", "World-class robotics. Teaching robots new skills, of course.", None, None),
     ("acme", "Acme", "Homework solutions for 6.141", None, "course_or_event"),
     ("labsco", "Labs Co", "We build robots", "https://ox.ac.uk", "academic")],
)
def test_noise_org_reason(login, name, desc, blog, reason):
    assert noise_org_reason(login, name, desc, blog) == reason


def test_is_list_repo_and_qualifying_members():
    assert is_list_repo({"name": "awesome-robot-learning"})
    assert is_list_repo({"name": "papers", "description": "A curated list of VLA papers"})
    assert not is_list_repo({"name": "core", "description": "Manipulation stack"})
    yo = load("young_orgs.json")["contributors"]
    m = qualifying_members({k: yo[k] for k in ("newbot-ai/core", "newbot-ai/sim")}, min_commits=3)
    assert list(m) == ["alice", "bob", "carol"]  # carol reaches 3 commits across both repos; bots excluded
    assert m["carol"]["repos"] == {"newbot-ai/core": 2, "newbot-ai/sim": 2}
    assert list(qualifying_members({"solo-org/proto": yo["solo-org/proto"]}, min_commits=3)) == ["dave"]


def _mock_young_orgs(router: respx.Router) -> dict[str, respx.Route]:
    yo = load("young_orgs.json")
    routes = {"search": router.get(f"{API}/search/repositories").respond(json=yo["search"])}
    for login, data in yo["orgs"].items():
        routes[f"org:{login}"] = router.get(f"{API}/orgs/{login}").respond(json=data)
    for login, data in yo["first_repo"].items():
        routes[f"first:{login}"] = router.get(f"{API}/orgs/{login}/repos").respond(json=data)
    for full, data in yo["contributors"].items():
        routes[f"contrib:{full}"] = router.get(f"{API}/repos/{full}/contributors").respond(json=data)
    for full in ("newbot-ai/core", "newbot-ai/sim", "repurposed/agent"):
        owner, name = full.split("/")
        repo = {**load("repo_org.json"), "full_name": full, "name": name, "html_url": f"https://github.com/{full}",
                "owner": {"login": owner, "type": "Organization"}, "created_at": "2026-08-03T10:00:00Z"}
        routes[f"repo:{full}"] = router.get(f"{API}/repos/{full}").respond(json=repo)
    routes["star_history"] = router.get(f"{API}/repos/newbot-ai/core/stargazers/history").respond(
        json=load("star_history_p1.json"), headers={"link": '<https://api.github.com/x?page=2>; rel="next"'})
    routes["star_page2"] = router.get("https://api.github.com/x").respond(
        json=load("star_history_p2.json"), headers={"link": '<https://api.github.com/y?page=3>; rel="next"'})
    routes["star_page3"] = router.get("https://api.github.com/y").respond(
        json=[], headers={"link": '<https://api.github.com/z?page=4>; rel="next"'})
    for u in ("alice", "bob", "carol"):
        routes[f"user:{u}"] = router.get(f"{API}/users/{u}").respond(json=load(f"user_{u}.json"))
    routes["other"] = router.route(url__startswith=API).respond(404, json={"message": "Not Found"})
    return routes


def test_discover_young_orgs_filters_and_emits_team_signal(provider):
    with respx.mock(assert_all_called=False) as router:
        routes = _mock_young_orgs(router)
        batch = provider.discover_young_orgs(["robot learning"])
        urls = [str(c.request.url) for c in router.calls]
    q = routes["search"].calls.last.request.url.params
    assert routes["search"].call_count == 1  # total_count under the 1,000 cap: no window split
    assert q["q"] == '"robot learning" created:>=2026-05-09 stars:>=3 fork:false'
    assert q["sort"] == "updated" and q["per_page"] == "100"
    # Name-level noise and list repos cost no core calls.
    assert not any("robo-curators" in u or "cs287" in u for u in urls)
    assert provider.young_org_report["course_or_event"] == ["cs287-course-2026"]
    assert provider.young_org_report["academic"] == ["xyz-grasp"]
    assert provider.young_org_report["too_few_contributors"] == ["solo-org"]
    assert provider.young_org_report["org_too_old"] == ["oldcorp"]
    assert batch.rejected == 4
    assert routes["contrib:solo-org/proto"].call_count == 1
    assert [u for u in urls if "xyz-grasp" in u] == [f"{API}/orgs/xyz-grasp"]  # academic: rejected after 1 call
    assert not any("jdoe" in u for u in urls)  # user-owned repos are never screened

    companies = {c.github_org: c for c in batch.companies}
    assert set(companies) == {"newbot-ai", "repurposed"}
    assert companies["repurposed"].attrs["young_org_basis"] == "first_public_repo_created_at"
    teams = {s.entity.key: s for s in batch.signals if s.signal_type == "young_org_team"}
    nb = teams["github_org:newbot-ai"]
    assert nb.title == "newbot-ai: 3 contributors with >=3 commits in a 66-day-old organization"
    assert [m["login"] for m in nb.metadata["members"]] == ["alice", "bob", "carol"]
    assert nb.metadata["age_basis"] == "org_created_at" and nb.raw_value == 3
    assert {gh_user_ref("alice"), gh_user_ref("bob"), gh_user_ref("carol")} <= set(nb.related)
    rp = teams["github_org:repurposed"]
    assert rp.title == "repurposed: 2 contributors with >=3 commits in a 118-day-old organization"
    assert rp.metadata["age_basis"] == "first_public_repo_created_at"
    assert not any(r.type == "person" for r in rp.related)  # frank/gina profiles 404: not linked
    # Existing org_created signal reused for the young org (not for the 2019 org).
    created = {s.entity.key for s in batch.signals if s.signal_type == "org_created"}
    assert created == {"github_org:newbot-ai"}
    # Members are self-reported profiles; carol's sim commits become a contributor edge.
    people = {p.github_handle: p for p in batch.people}
    assert {"alice", "bob", "carol"} <= set(people) and all(p.attrs["self_reported"] for p in people.values())
    edges = {(r.src.key, r.dst.key) for r in batch.relations if r.kind == "contributor"}
    assert ("github_user:carol", "repo:newbot-ai/sim") in edges
    # The shallow repo has no stats: its members' newest commits are probed (404 here -> no dates invented).
    assert any("/repos/newbot-ai/sim/commits?author=carol" in u for u in urls)
    sim = next(r for r in batch.relations if r.kind == "contributor" and r.dst == repo_ref("newbot-ai/sim"))
    assert "last_commit_at" not in sim.attrs and "first_commit_at_approximate" not in sim.attrs
    # Young repo: star history capped at 3 pages even with a token.
    assert routes["star_history"].call_count == 1 and routes["star_page3"].call_count == 1
    assert not any("page=4" in str(c.request.url) or str(c.request.url).endswith("/z") for c in routes["other"].calls)


def test_discover_young_orgs_splits_windows_over_result_cap(provider):
    with respx.mock(assert_all_called=False) as router:
        search = router.get(f"{API}/search/repositories").mock(side_effect=[
            httpx.Response(200, json={"total_count": 2500, "items": []}),
            httpx.Response(200, json={"total_count": 600, "items": []}),
            httpx.Response(200, json={"total_count": 900, "items": []}),
        ])
        batch = provider.discover_young_orgs(["vla"], max_searches=3)
    qs = [c.request.url.params["q"] for c in search.calls]
    assert qs == ["vla created:>=2026-05-09 stars:>=3 fork:false",
                  "vla created:2026-07-24..2026-10-06 stars:>=3 fork:false",
                  "vla created:2026-05-09..2026-07-23 stars:>=3 fork:false"]
    assert batch.companies == [] and batch.errors == []


def test_discover_young_orgs_stops_on_rate_limit(provider):
    with respx.mock(assert_all_called=False) as router:
        router.get(f"{API}/search/repositories").respond(
            403, json={"message": "API rate limit exceeded"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999999999"})
        batch = provider.discover_young_orgs(["vla", "robot"])
    assert len(batch.errors) == 1 and "rate limit" in batch.errors[0]


def test_ai_coding_agents_are_not_people():
    from radar.providers.github import is_bot

    for login in ("claude", "Copilot", "devin-ai-integration", "cursoragent", "google-labs-jules", "coderabbitai"):
        assert is_bot(login), login
    assert not is_bot("claudia") and not is_bot("torvalds")

"""X provider family: classification, official API client, manual oEmbed ingest, demo-only mock."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
import respx

from radar.providers.base import EntityRef
from radar.providers.http import HttpClient
from radar.providers.x.api import XApiProvider
from radar.providers.x.base import (
    DisabledXProvider,
    XPost,
    XUser,
    classify_post,
    get_x_provider,
    posts_to_batch,
    snowflake_time,
)
from radar.providers.x.manual import InvalidPostURL, ManualXSignalProvider, parse_oembed, parse_post_url
from radar.providers.x.mock import MockXProvider, NotADemoHandle
from radar.settings import Settings
from tests.conftest import FIXTURES

X = FIXTURES / "x"
OEMBED_ROUTE = "https://publish.twitter.com/oembed"


def _fx(name: str):
    return json.loads((X / name).read_text())


# ── classification / batch conversion ──
@pytest.mark.parametrize(
    "text, expected",
    [
        ("Just left Big Lab. Working on something new.", "x_formation_post"),
        ("We’re hiring a founding engineer", "x_formation_post"),
        ("Launching our new robot arm today", "x_launch_post"),  # 'launching' alone is not formation
        ("Introducing Handy-1", "x_launch_post"),
        ("We're hiring controls engineers", "x_hiring_post"),
        ("Join our team in Austin", "x_hiring_post"),
        ("Nice weather today", "x_post"),
    ],
)
def test_classify_post(thesis, text, expected):
    assert classify_post(text, thesis)[0] == expected


def test_snowflake_time():
    assert snowflake_time("2105674093163446272") == datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    assert snowflake_time("20") is None and snowflake_time("abc") is None


def test_posts_to_batch(thesis):
    ref = EntityRef(type="person", key="x:testfounder")
    user = XUser(username="testfounder", name="Test Founder", followers_count=1834)
    post = XPost(id="2105674093163446272", author_username="testfounder", text="Day 1 at a new thing. In stealth.",
                 created_at=datetime(2026, 10, 1, 15, tzinfo=UTC),
                 url="https://x.com/testfounder/status/2105674093163446272")
    batch = posts_to_batch([post], user, ref, "x_api", thesis=thesis)
    (sig,) = batch.signals
    assert sig.signal_type == "x_formation_post" and sig.epistemic == "FACT" and sig.source_type == "x"
    assert sig.source_url == post.url and sig.event_at == post.created_at
    assert sig.metadata["matched_phrases"] == ["stealth", "day 1 at"]
    assert batch.metrics[0].metric == "followers" and batch.metrics[0].value == 1834
    assert batch.people[0].ref == ref and batch.people[0].x_handle == "testfounder"
    # Subject is a different entity → no record is asserted for it from the poster's profile.
    other = posts_to_batch([post], user, EntityRef(type="company", key="domain:acme.com"), "x_api", thesis=thesis)
    assert other.people == [] and other.companies == []


def test_disabled_provider_reason():
    p = get_x_provider(Settings(x_bearer_token=None))
    assert isinstance(p, DisabledXProvider)
    assert p.enabled() == (False, "X_BEARER_TOKEN required — official X API v2 access")
    assert p.recent_posts("anyone") == [] and p.get_user("anyone") is None
    assert isinstance(get_x_provider(Settings(x_bearer_token="t")), XApiProvider)


# ── official API ──
@respx.mock
def test_api_user_and_posts():
    respx.get("https://api.x.com/2/users/by/username/testfounder").mock(
        return_value=httpx.Response(200, json=_fx("api_user.json")))
    tweets = respx.get("https://api.x.com/2/users/1500000000000000001/tweets").mock(
        return_value=httpx.Response(200, json=_fx("api_tweets.json")))
    api = XApiProvider("tok", client=HttpClient(max_retries=0))
    user = api.get_user("testfounder")
    assert user.followers_count == 1834 and user.id == "1500000000000000001"
    posts = api.recent_posts("testfounder", limit=10)
    assert posts[0].url == "https://x.com/testfounder/status/2105674093163446272"
    req = tweets.calls.last.request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.url.params["max_results"] == "10" and "created_at" in req.url.params["tweet.fields"]


@respx.mock
def test_api_search_never_guesses_authors():
    respx.get("https://api.x.com/2/tweets/search/recent").mock(
        return_value=httpx.Response(200, json=_fx("api_search.json")))
    posts = XApiProvider("tok", client=HttpClient(max_retries=0)).search_recent("robotics hiring")
    assert [(p.author_username, p.id) for p in posts] == [("acmerobotics", "2104541631083446272")]


@respx.mock
def test_api_rate_limit_stops_calls():
    route = respx.get("https://api.x.com/2/users/by/username/a").mock(
        return_value=httpx.Response(429, headers={"x-rate-limit-reset": "1791000000"}))
    api = XApiProvider("tok", client=HttpClient(max_retries=0))
    assert api.get_user("a") is None
    assert api.recent_posts("a") == [] and api.search_recent("x") == []
    assert route.call_count == 1
    ok, reason = api.enabled()
    assert not ok and "rate limit" in reason


def test_api_rejects_invalid_handle():
    assert XApiProvider("tok").get_user("../../admin") is None


# ── manual ingest ──
@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/testfounder/status/2105674093163446272",
        "https://twitter.com/testfounder/status/2105674093163446272/",
        "https://mobile.twitter.com/testfounder/status/2105674093163446272?s=20",
    ],
)
def test_parse_post_url_valid(url):
    assert parse_post_url(url) == ("testfounder", "2105674093163446272")


@pytest.mark.parametrize(
    "url",
    [
        "http://x.com/a/status/1",
        "https://evil.com/a/status/1",
        "https://x.com.evil.com/a/status/1",
        "https://user@x.com/a/status/1",
        "https://x.com:8443/a/status/1",
        "https://x.com/a/status/12abc",
        "https://x.com/a/likes",
        "https://x.com/i/status/123",
        "https://x.com/waytoolonghandle_123/status/1",
        "javascript:alert(1)",
        "",
    ],
)
def test_parse_post_url_invalid(url):
    with pytest.raises(InvalidPostURL):
        parse_post_url(url)


def test_parse_oembed_real_fixture():
    parsed = parse_oembed(_fx("oembed_jack_20.json"))
    assert parsed["handle"] == "jack" and parsed["text"] == "just setting up my twttr"
    assert parsed["shown_date"].date().isoformat() == "2006-03-21"


@respx.mock
def test_manual_ingest_oembed(thesis):
    route = respx.get(OEMBED_ROUTE).mock(return_value=httpx.Response(200, json=_fx("oembed_formation.json")))
    m = ManualXSignalProvider(client=HttpClient(max_retries=0), thesis=thesis)
    # Wrong handle in the URL: oEmbed reports the real author, which wins.
    batch, info = m.ingest("https://x.com/somebodyelse/status/2105674093163446272")
    assert "twitter.com%2Fsomebodyelse%2Fstatus%2F2105674093163446272" in str(route.calls.last.request.url)
    assert info["text_source"] == "oembed" and info["handle_corrected_from"] == "somebodyelse"
    (sig,) = batch.signals
    assert sig.entity.key == "x:testfounder" and sig.entity.type == "person"
    assert sig.signal_type == "x_formation_post"
    assert sig.source_url == "https://x.com/testfounder/status/2105674093163446272"
    assert sig.event_at == datetime(2026, 10, 1, 15, 0, tzinfo=UTC)
    assert sig.confidence == 1.0
    assert sig.metadata["ingest"] == "manual" and sig.metadata["provenance"] == "x_oembed"
    assert "we're hiring" in sig.metadata["matched_phrases"] and "just left" in sig.metadata["matched_phrases"]
    assert "&amp;" not in sig.description and "& we’re hiring" in sig.description
    assert batch.people[0].name == "Test Founder"


@respx.mock
def test_manual_ingest_pasted_fallback_is_labeled(thesis):
    respx.get(OEMBED_ROUTE).mock(return_value=httpx.Response(404))
    m = ManualXSignalProvider(client=HttpClient(max_retries=0), thesis=thesis)
    company = EntityRef(type="company", key="domain:acme-robotics.com")
    batch, info = m.ingest("https://x.com/acmerobotics/status/2104541631083446272",
                           pasted_text="We're hiring controls engineers", entity_ref=company)
    (sig,) = batch.signals
    assert info["text_source"] == "user_provided"
    assert sig.entity == company and sig.signal_type == "x_hiring_post"
    assert sig.metadata["provenance"] == "user_provided" and sig.metadata["handle_verified"] is False
    assert sig.confidence == 0.6


@respx.mock
def test_manual_ingest_nothing_available_errors(thesis):
    respx.get(OEMBED_ROUTE).mock(return_value=httpx.Response(404))
    m = ManualXSignalProvider(client=HttpClient(max_retries=0), thesis=thesis)
    batch, info = m.ingest("https://x.com/acmerobotics/status/2104541631083446272")
    assert batch.signals == [] and "error" in info
    batch, info = m.ingest("https://example.com/a/status/1", pasted_text="anything")
    assert batch.signals == [] and "error" in info


# ── demo mock ──
def test_mock_serves_only_demo_fixtures(thesis):
    mock = MockXProvider(_fx("demo_fixtures.json"))
    assert mock.enabled() == (True, "demo fixtures only")
    batch = mock.to_batch("demo_founder", thesis=thesis)
    assert {s.signal_type for s in batch.signals} == {"x_formation_post", "x_launch_post"}
    assert all(s.metadata["synthetic"] is True and s.source_url is None for s in batch.signals)
    assert batch.metrics[0].source == "demo"
    with pytest.raises(NotADemoHandle):
        mock.recent_posts("elonmusk")
    with pytest.raises(NotADemoHandle):
        MockXProvider([{"handle": "realperson", "posts": [{"id": "1", "text": "hi", "created_at": "2026-01-01"}]}])


# ── V2: names, seeds config, official discovery, sweep step ──
from datetime import date  # noqa: E402

from radar.providers.x.api import OfficialXProvider  # noqa: E402
from radar.providers.x.discovery import (  # noqa: E402
    XSeeds,
    build_queries,
    discover,
    keep_post,
    load_x_seeds,
    matched_discovery_phrases,
    rotate,
)
from radar.providers.x.manual import ManualXProvider  # noqa: E402

SEARCH = "https://api.x.com/2/tweets/search/recent"
NOW = datetime(2026, 10, 20, 12, tzinfo=UTC)
DAY = date(2026, 10, 20)


def test_v2_names_and_aliases():
    assert XApiProvider is OfficialXProvider and ManualXSignalProvider is ManualXProvider
    assert isinstance(get_x_provider(Settings(x_bearer_token="t")), OfficialXProvider)
    assert isinstance(get_x_provider(Settings(x_bearer_token=None)), DisabledXProvider)


def test_repo_x_seeds_config_builds_valid_queries():
    seeds = load_x_seeds(FIXTURES.parents[2] / "config" / "x_seeds.yaml")
    assert seeds.seed_accounts == []  # empty by default
    for p in ("building something new", "starting something", "we're hiring", "founding engineer", "new company",
              "left {company}", "new project", "new paper", "launching today", "stealth"):
        assert p in seeds.phrases
    qs = build_queries(seeds)
    assert qs and all(len(q.text) <= seeds.limits.max_query_chars for q in qs)
    assert {q.phrase for q in qs} == set(seeds.phrases)
    left = next(q for q in qs if q.phrase == "left {company}")
    assert '"left OpenAI"' in left.text and "{company}" not in left.text
    assert all(q.text.endswith("-is:retweet -is:reply lang:en") for q in qs)
    # every thesis term lands in at least one query of each phrase
    for phrase in seeds.phrases:
        joined = " ".join(q.text for q in qs if q.phrase == phrase)
        assert all(t.split()[0] in joined for t in seeds.thesis_terms)


def test_query_chunking_respects_length_limit():
    seeds = XSeeds(phrases=["new paper"], thesis_terms=[f"term{i}" for i in range(60)],
                   limits={"max_query_chars": 120}, query_suffix="-is:retweet")
    qs = build_queries(seeds)
    assert len(qs) > 1 and all(len(q.text) <= 120 for q in qs)
    assert qs[0].text.startswith('"new paper" (term0 OR term1')


def test_seed_accounts_are_validated():
    assert XSeeds(seed_accounts=["@Some_Handle", "some_handle"]).seed_accounts == ["Some_Handle"]
    with pytest.raises(ValueError):
        XSeeds(seed_accounts=["not a handle!"])


def test_rotate_covers_everything_over_days():
    items = list(range(7))
    seen: set[int] = set()
    for d in range(4):
        win = rotate(items, 2, date(2026, 10, 1 + d))
        assert len(win) == 2
        seen |= set(win)
    assert seen == set(items) and rotate(items, 0, DAY) == [] and rotate(items[:2], 5, DAY) == items[:2]


def test_discovery_phrase_matching_and_keep_rule(thesis):
    seeds = XSeeds(phrases=["new paper", "left {company}"], departure_companies=["OpenAI"])
    assert matched_discovery_phrases("Our NEW  paper on tactile sensing is out", seeds) == ["new paper"]
    assert matched_discovery_phrases("I left OpenAI last week", seeds) == ["left OpenAI"]
    assert matched_discovery_phrases("renewed papers", seeds) == []
    mk = lambda t: XPost(id="1", author_username="a", text=t, created_at=NOW, url="https://x.com/a/status/1")  # noqa: E731
    assert keep_post(mk("Our new paper on robot learning"), thesis, seeds) == (True, ["new paper"])
    assert keep_post(mk("Working on something new in robotics"), thesis, seeds)[0] is True  # formation phrase
    assert keep_post(mk("Great robotics talk today"), thesis, seeds) == (False, [])


def _search_body(posts, users):
    return {"data": posts, "includes": {"users": users}, "meta": {"result_count": len(posts)}}


P1 = {"id": "2105674093163446272", "text": "Just left OpenAI. Building something new in robotics. Stealth for now.",
      "created_at": "2026-10-18T15:00:00.000Z", "author_id": "11", "public_metrics": {"like_count": 40}}
P2 = {"id": "2105674093163446273", "text": "Nice robotics demo at the conference",  # generic → dropped
      "created_at": "2026-10-18T16:00:00.000Z", "author_id": "12"}
P3 = {"id": "2105674093163446274", "text": "New paper: tactile world models for dexterous manipulation",
      "created_at": "2026-10-19T09:00:00.000Z", "author_id": "11"}
ORPHAN = {"id": "2105674093163446275", "text": "stealth robotics co", "created_at": "2026-10-19T09:00:00.000Z",
          "author_id": "999"}
U11 = {"id": "11", "username": "AdaBuilds", "name": "Ada", "description": "ex-OpenAI. robots.",
       "public_metrics": {"followers_count": 812}}
U12 = {"id": "12", "username": "confbot", "name": "Conf"}


def _seeds(**kw):
    base = {"phrases": ["stealth", "new paper", "building something new"], "thesis_terms": ["robotics", "tactile"],
            "limits": {"request_budget": 3, "max_results": 10}}
    base.update(kw)
    return XSeeds.model_validate(base)


@respx.mock
def test_official_discovery_maps_posts_to_people_with_real_urls(thesis):
    bodies = [_search_body([P1, P2, ORPHAN], [U11, U12]), _search_body([P3, P1], [U11]), _search_body([], [])]
    route = respx.get(SEARCH).mock(side_effect=[
        httpx.Response(200, json=b, headers={"x-rate-limit-remaining": str(400 - i), "x-rate-limit-reset": "1791000000"})
        for i, b in enumerate(bodies)])
    api = OfficialXProvider("tok", client=HttpClient(max_retries=0))
    batch, info = discover(api, _seeds(), thesis=thesis, day=DAY, now=NOW)
    assert route.call_count == 3 and info["requests"] == 3 and info["stopped"] == "request_budget"
    req = route.calls[0].request
    assert req.headers["Authorization"] == "Bearer tok"
    assert req.url.params["start_time"] == "2026-10-13T12:01:00Z" and req.url.params["max_results"] == "10"
    assert "username" in req.url.params["user.fields"] and "author_id" in req.url.params["expansions"]
    # P1 deduped across queries, P2 generic dropped, orphan (no author expansion) never attributed
    assert info["posts_seen"] == 3 and info["posts_kept"] == 2 and info["posts_dropped"] == 1
    assert {s.entity.key for s in batch.signals} == {"x:adabuilds"}
    urls = {s.source_url for s in batch.signals}
    assert urls == {"https://x.com/AdaBuilds/status/2105674093163446272",
                    "https://x.com/AdaBuilds/status/2105674093163446274"}
    by_id = {s.metadata["post_id"]: s for s in batch.signals}
    assert by_id[P1["id"]].signal_type == "x_formation_post" and by_id[P1["id"]].epistemic == "FACT"
    assert by_id[P1["id"]].metadata["source"] == "x_api" and by_id[P1["id"]].metadata["ingest"] == "x_search"
    assert by_id[P3["id"]].metadata["discovery_phrases"] == ["new paper"]
    assert [p.ref.key for p in batch.people] == ["x:adabuilds"] and batch.people[0].followers == 812
    assert len(batch.metrics) == 1 and batch.errors == [] and batch.provider == "x"
    assert info["rate_limit_remaining"] == 398 and api.client.stats.rate_limit_remaining == 398


@respx.mock
def test_discovery_stops_cleanly_when_quota_exhausted(thesis):
    route = respx.get(SEARCH).mock(return_value=httpx.Response(
        200, json=_search_body([P1], [U11]), headers={"x-rate-limit-remaining": "0", "x-rate-limit-reset": "1791000000"}))
    api = OfficialXProvider("tok", client=HttpClient(max_retries=0))
    batch, info = discover(api, _seeds(), thesis=thesis, day=DAY, now=NOW)
    assert route.call_count == 1  # data of the last allowed call is kept, nothing more is requested
    assert info["stopped"] == "rate_limited" and info["rate_limited_until"].startswith("2026-")
    assert len(batch.signals) == 1 and batch.errors == []


@respx.mock
def test_discovery_429_is_a_clean_stop_not_an_error(thesis):
    route = respx.get(SEARCH).mock(return_value=httpx.Response(429, headers={"x-rate-limit-reset": "1791000000"}))
    api = OfficialXProvider("tok", client=HttpClient(max_retries=0))
    batch, info = discover(api, _seeds(), thesis=thesis, day=DAY, now=NOW)
    assert route.call_count == 1 and info["stopped"] == "rate_limited"
    assert batch.errors == [] and batch.signals == [] and api.client.stats.errors == 0


@respx.mock
def test_discovery_records_access_errors(thesis):
    respx.get(SEARCH).mock(return_value=httpx.Response(403, json={"title": "Forbidden"}))
    batch, info = discover(OfficialXProvider("tok", client=HttpClient(max_retries=0)), _seeds(), thesis=thesis,
                           day=DAY, now=NOW)
    assert len(batch.errors) == 3 and all("HTTP 403" in e for e in batch.errors) and info["stopped"] == "request_budget"


@respx.mock
def test_discovery_reads_seed_accounts_within_budget(thesis):
    respx.get("https://api.x.com/2/users/by/username/testfounder").mock(
        return_value=httpx.Response(200, json=_fx("api_user.json")))
    respx.get("https://api.x.com/2/users/1500000000000000001/tweets").mock(
        return_value=httpx.Response(200, json=_fx("api_tweets.json")))
    search = respx.get(SEARCH).mock(return_value=httpx.Response(200, json=_search_body([], [])))
    seeds = _seeds(seed_accounts=["testfounder"], limits={"request_budget": 4, "max_results": 10})
    api = OfficialXProvider("tok", client=HttpClient(max_retries=0))
    batch, info = discover(api, seeds, thesis=thesis, day=DAY, now=NOW)
    assert info["seed_accounts"][0]["handle"] == "testfounder" and search.call_count == 2 and info["requests"] == 4
    assert batch.signals and all(s.entity.key == "x:testfounder" for s in batch.signals)
    assert all(s.metadata["ingest"] == "x_seed" for s in batch.signals)
    assert all(s.signal_type != "x_post" or s.metadata["discovery_phrases"] for s in batch.signals)


def test_x_step_is_in_sweep_and_cli_lists():
    from radar import cli, ops

    assert "x" in ops.SWEEP_STEPS and "x" in ops.ALL_STEPS and cli._ALL_STEPS == ops.ALL_STEPS
    assert ops.ALL_STEPS.index("x") < ops.ALL_STEPS.index("score")


def test_sweep_step_without_token_records_skipped_run(db, monkeypatch):
    from sqlalchemy import select

    from radar import ops
    from radar.models import IngestionRun
    from radar.providers.x import sweep as xsweep
    from radar.services.status import system_status

    monkeypatch.setattr(xsweep, "get_settings", lambda: Settings(x_bearer_token=None))
    out = ops.run_step("x")
    assert out["status"] == "skipped"
    db.expire_all()
    [run] = db.scalars(select(IngestionRun).where(IngestionRun.provider == "x")).all()
    assert run.status == "skipped" and run.errors == ["API access not configured"] and run.finished_at
    monkeypatch.setattr("radar.services.status.get_settings", lambda: Settings(x_bearer_token=None))
    x = next(p for p in system_status(db).providers if p.provider == "x")
    assert x.state == "disabled" and "not configured" in (x.reason or "")


@respx.mock
def test_sweep_step_with_token_runs_discovery(db, thesis):
    from sqlalchemy import select

    from radar.models import IngestionRun, Signal
    from radar.providers.x.sweep import ingest_x_sweep

    respx.get(SEARCH).mock(return_value=httpx.Response(200, json=_search_body([P1], [U11]),
                                                       headers={"x-rate-limit-remaining": "120"}))
    api = OfficialXProvider("tok", client=HttpClient(max_retries=0))
    run, _ = ingest_x_sweep(Settings(x_bearer_token="tok"), seeds=_seeds(limits={"request_budget": 1}), provider=api)
    assert run.status == "ok" and run.provider == "x" and run.request_count == 1 and run.rate_limit_remaining == 120
    assert run.params["mode"] == "official_search" and run.params["posts_kept"] == 1
    db.expire_all()
    [sig] = db.scalars(select(Signal).where(Signal.source_type == "x")).all()
    assert sig.source_url == "https://x.com/AdaBuilds/status/2105674093163446272"
    assert db.scalars(select(IngestionRun)).one().status == "ok"

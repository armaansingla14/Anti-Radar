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

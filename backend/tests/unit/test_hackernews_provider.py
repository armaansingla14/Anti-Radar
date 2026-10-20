"""Hacker News provider: title parsing, link resolution and signals from recorded Algolia responses."""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime

import httpx
import pytest

from radar.providers.base import domain_ref, paper_ref, repo_ref
from radar.providers.hackernews import HackerNewsProvider, build_batch, hn_strength, parse_hn_title
from tests.conftest import FIXTURES

NOW = datetime(2026, 10, 6, 16, tzinfo=UTC)


def _hits(name: str) -> list[dict]:
    return json.loads((FIXTURES / "hn" / f"{name}.json").read_text())["hits"]


@pytest.mark.parametrize(
    "title,expected",
    [
        (
            "Launch HN: Nori Robotics (YC S26) – A low-cost humanoid robot for development",
            ("launch_hn", "Nori Robotics", "A low-cost humanoid robot for development", "S26"),
        ),
        (
            "Launch HN: ProvenMetal (YC S26) delivers circuit boards in days instead of weeks",
            ("launch_hn", "ProvenMetal", "delivers circuit boards in days instead of weeks", "S26"),
        ),
        ("Launch HN: Acme (YC X25)", ("launch_hn", "Acme", None, "X25")),
        ("Launch HN: Acme (YC W2024) - Robots", ("launch_hn", "Acme", "Robots", "W2024")),
        (
            "Show HN: Jotbus – a shared encrypted scratchpad",
            ("show_hn", "Jotbus", "a shared encrypted scratchpad", None),
        ),
        ("Show HN: Acme — robots for farms", ("show_hn", "Acme", "robots for farms", None)),
        ("Show HN: Acme—robots for farms", ("show_hn", "Acme", "robots for farms", None)),
        ("Show HN: Acme - robots for farms", ("show_hn", "Acme", "robots for farms", None)),
        ("Show HN: Acme: robots for farms", ("show_hn", "Acme", "robots for farms", None)),
        ("Show HN – Acme – robots", ("show_hn", "Acme", "robots", None)),
        ("show hn:   Acme   –  robots", ("show_hn", "Acme", "robots", None)),
        (
            "Show HN: Parseable, an open observability datalake",
            ("show_hn", "Parseable", "an open observability datalake", None),
        ),
        # Hyphenated words are not separators.
        (
            "Show HN: Open-source tactile sensor firmware",
            ("show_hn", None, "Open-source tactile sensor firmware", None),
        ),
        # Sentences are not product names.
        (
            "Show HN: I finetuned 1.5B Qwen to near GPT-4o level bash generation perf",
            ("show_hn", None, "I finetuned 1.5B Qwen to near GPT-4o level bash generation perf", None),
        ),
        (
            "Show HN: I built a robot – it folds laundry",
            ("show_hn", None, "I built a robot – it folds laundry", None),
        ),
        (
            "Show HN: A Walkable O'Neill Cylinder in the Browser",
            ("show_hn", None, "A Walkable O'Neill Cylinder in the Browser", None),
        ),
        (
            "Show HN: Pumpkins.sh – claim, carve, and display a pumpkin",
            ("show_hn", "Pumpkins.sh", "claim, carve, and display a pumpkin", None),
        ),
        ("Ask HN: Who is hiring?", ("story", None, "Ask HN: Who is hiring?", None)),
        ("Why humanoid robots won't catch up", ("story", None, "Why humanoid robots won't catch up", None)),
        ("", ("story", None, None, None)),
    ],
)
def test_parse_hn_title(title, expected):
    assert parse_hn_title(title) == expected


def test_strength_curve():
    assert hn_strength(0) == 0
    assert hn_strength(999) == pytest.approx(1.0)
    assert hn_strength(5000) == 1.0
    assert hn_strength(99) == pytest.approx(math.log10(100) / 3)


def test_launches_resolve_to_company_repo_or_text_link():
    b = build_batch(_hits("launch_hn_page0"), now=NOW)
    assert b.rejected == 0
    sig = {s.metadata["story_id"]: s for s in b.signals}
    assert {s.signal_type for s in b.signals} == {"hn_launch"}

    nori = sig["49525153"]
    assert nori.entity == domain_ref("norirobotics.com")
    assert nori.raw_value == 201 and nori.normalized_value == pytest.approx(math.log10(202) / 3)
    assert nori.source_url == "https://news.ycombinator.com/item?id=49525153"
    assert nori.metadata["author"] and nori.metadata["num_comments"] == 66
    hours = (NOW - nori.event_at).total_seconds() / 3600
    assert nori.metadata["comment_velocity"] == pytest.approx(66 / hours, abs=1e-3)
    company = next(c for c in b.companies if c.ref == nori.entity)
    assert (company.name, company.kind, company.website) == (
        "Nori Robotics",
        "company",
        "https://www.norirobotics.com",
    )
    assert (
        company.attrs["yc_batch"] == "S26"
        and company.description == "A low-cost humanoid robot for development"
    )

    # GitHub link -> repo stub entity, not a company.
    mag = sig["49911995"]
    assert mag.entity == repo_ref("magnitudedev/magnitude")
    assert next(r for r in b.repositories if r.ref == mag.entity).attrs["stub"] is True

    # Self-post: website taken from post text only because it matches the product name.
    salem = sig["49466715"]
    assert salem.entity == domain_ref("salemroboticsinc.com")
    assert salem.metadata["link_source"] == "story_text" and salem.confidence < 1

    # Blog post URL -> company website is the origin.
    vespper = next(c for c in b.companies if c.ref == domain_ref("vespper.com"))
    assert vespper.website == "https://www.vespper.com"


def test_no_people_from_hn_usernames():
    b = build_batch(_hits("launch_hn_page0") + _hits("show_hn_page0"), now=NOW)
    assert b.people == []
    assert all(s.metadata.get("author") for s in b.signals)


def test_show_hn_platform_and_hosting_links_never_become_companies():
    b = build_batch(_hits("show_hn_page0"), now=NOW)
    keys = {c.ref.key for c in b.companies}
    assert not any(k.endswith(("youtube.com", "github.com", "fly.dev")) for k in keys)
    assert all(c.kind == "project" for c in b.companies)
    # The YouTube-only story and the fly.dev-hosted app have no resolvable entity.
    assert "49978398" not in {s.metadata["story_id"] for s in b.signals}
    assert "49966745" not in {s.metadata["story_id"] for s in b.signals}
    assert b.rejected == 2
    # Name not derivable from title -> domain label.
    assert next(c for c in b.companies if c.ref == domain_ref("dirac.run")).name == "dirac"


def test_discussion_links_to_arxiv_become_paper_stubs():
    b = build_batch(_hits("topic_arxiv"), now=NOW)
    sig = {s.metadata["story_id"]: s for s in b.signals}
    s = sig["49580164"]
    assert s.signal_type == "hn_discussion" and s.entity == paper_ref("2609.03344")
    stub = next(p for p in b.papers if p.ref == s.entity)
    assert stub.attrs["stub"] is True and stub.title == "arXiv 2609.03344"  # HN title is not the paper title
    # blog.arxiv.org post is not a paper and a discussion never creates a company.
    assert "49823664" not in sig
    assert b.companies == []


def test_discussion_to_news_site_is_skipped():
    b = build_batch(_hits("topic_humanoid"), now=NOW)
    assert [c.ref for c in b.companies] == [
        domain_ref("norirobotics.com"),
        domain_ref("salemroboticsinc.com"),
    ]
    assert b.rejected == 1  # understandingai.org article


def test_dedup_and_metrics():
    hits = _hits("launch_hn_page0")
    b = build_batch(hits + hits[:3], now=NOW)
    assert len(b.signals) == len(hits)
    m = next(m for m in b.metrics if m.entity == domain_ref("norirobotics.com"))
    assert (m.metric, m.day.isoformat(), m.value, m.source) == ("hn_points", "2026-09-01", 201, "observed")


def test_malformed_hits_rejected():
    b = build_batch(
        [{"objectID": "1", "title": "Show HN: X – y"}, {"title": "no id", "created_at_i": 1}], now=NOW
    )
    assert b.rejected == 2 and b.signals == []


def test_comment_velocity_none_when_post_is_in_future():
    hit = dict(_hits("launch_hn_page0")[0])
    b = build_batch([hit], now=datetime(2026, 1, 1, tzinfo=UTC))
    assert b.signals[0].metadata["comment_velocity"] is None


class _StubClient:
    def __init__(self, pages: dict[tuple[str, int], dict] | None = None, fail: bool = False):
        self.pages, self.fail, self.calls = pages or {}, fail, []

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        if self.fail:
            raise httpx.ConnectError("down")
        key = (params.get("tags") or "", params["page"])
        return self.pages.get(key, {"hits": [], "nbPages": 0})


def test_fetch_launches_queries_both_tags_with_filters(monkeypatch):
    monkeypatch.setattr("radar.providers.hackernews.clock.now", lambda: NOW)
    launch = json.loads((FIXTURES / "hn" / "launch_hn_page0.json").read_text())
    show = json.loads((FIXTURES / "hn" / "show_hn_page0.json").read_text())
    client = _StubClient({("launch_hn", 0): launch, ("show_hn", 0): {**show, "nbPages": 1}})
    b = HackerNewsProvider(client=client).fetch_launches(days=7, min_points=5)
    since = int(NOW.timestamp()) - 7 * 86400
    assert [(p["tags"], p["page"]) for _, p in client.calls] == [("launch_hn", 0), ("show_hn", 0)]
    assert client.calls[0][1]["numericFilters"] == f"created_at_i>{since},points>=5"
    assert {s.signal_type for s in b.signals} == {"hn_launch", "hn_show"}
    assert b.errors == []


def test_mentions_exact_match_and_error_reporting(monkeypatch):
    monkeypatch.setattr("radar.providers.hackernews.clock.now", lambda: NOW)
    page = json.loads((FIXTURES / "hn" / "topic_humanoid.json").read_text())
    client = _StubClient({("story", 0): {**page, "nbPages": 1}})
    m = HackerNewsProvider(client=client).mentions("norirobotics.com")
    assert client.calls[0][0].endswith("/search_by_date")
    assert m["count"] == 1 and m["points_total"] == 201 and m["error"] is None
    assert m["stories"][0]["objectID"] == "49525153"

    down = HackerNewsProvider(client=_StubClient(fail=True)).mentions("norirobotics.com")
    assert down["count"] == 0 and down["error"]


# ── formation-anchor enrichment: probe + stories that link to the org ───────


def test_story_links_to_requires_a_link_into_the_org():
    from radar.providers.hackernews import story_links_to

    gh = {"url": "https://github.com/FormingCo/engine", "links": ["https://github.com/FormingCo/engine"]}
    assert story_links_to(gh, github_owner="formingco") == "https://github.com/FormingCo/engine"
    pages = {"url": "https://formingco.github.io/demo", "links": ["https://formingco.github.io/demo"]}
    assert story_links_to(pages, github_owner="formingco")
    text = {"url": None, "links": ["https://docs.formingco.ai/start"]}
    assert story_links_to(text, domain="formingco.ai") == "https://docs.formingco.ai/start"
    # Naming the org, or a repo that merely contains the login, is not a link to it.
    other = {"url": "https://github.com/someone/formingco-clone",
             "links": ["https://github.com/someone/formingco-clone", "https://notformingco.ai/"]}
    assert story_links_to(other, github_owner="formingco", domain="formingco.ai") is None
    assert story_links_to({"url": None, "links": []}, github_owner="formingco") is None


def test_org_attention_metrics_and_linked_signal(monkeypatch):
    from radar.providers.base import gh_org_ref

    monkeypatch.setattr("radar.providers.hackernews.clock.now", lambda: NOW)
    monkeypatch.setattr("radar.providers.hackernews.clock.today", lambda: NOW.date())
    page = json.loads((FIXTURES / "hn" / "topic_humanoid.json").read_text())
    client = _StubClient({("story", 0): {**page, "nbPages": 1}})
    ref = gh_org_ref("norirobotics")
    b = HackerNewsProvider(client=client).org_attention(
        ref, ["norirobotics.com", "norirobotics"], github_owner="norirobotics", domain="norirobotics.com")
    metrics = {m.metric: m.value for m in b.metrics}
    assert metrics == {"hn_probe_mentions": 1.0, "hn_probe_points": 201.0}  # deduplicated across terms
    [sig] = b.signals
    assert sig.entity == ref and sig.signal_type == "hn_launch" and sig.raw_value == 201
    assert sig.source_url == "https://news.ycombinator.com/item?id=49525153"
    assert sig.metadata["link"] == "https://www.norirobotics.com/" and sig.metadata["matched_via"] == "org_link"
    assert sig.event_at.date().isoformat() == "2026-09-01" and b.errors == []

    # Mentioned but not linked: counted as attention, never a signal.
    b2 = HackerNewsProvider(client=client).org_attention(ref, ["norirobotics.com"], domain="elsewhere.ai")
    assert b2.signals == [] and {m.metric: m.value for m in b2.metrics}["hn_probe_mentions"] == 1.0


def test_org_attention_outage_emits_no_zero_metrics(monkeypatch):
    from radar.providers.base import gh_org_ref

    b = HackerNewsProvider(client=_StubClient(fail=True)).org_attention(gh_org_ref("x"), ["xco.ai"], domain="xco.ai")
    assert b.metrics == [] and b.signals == [] and b.errors

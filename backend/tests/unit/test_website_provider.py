"""Website crawl: extraction, page discovery, identity links, jobs, signals, robots, formation phrases."""

from __future__ import annotations

import json
import socket

import httpx
import pytest
import respx

from radar.providers.formation import detect_formation_phrases
from radar.providers.http import HttpClient, UnsafeURLError
from radar.providers.website import (
    Link,
    WebsiteProvider,
    classify_page_kind,
    discover_pages,
    extract_page,
    github_org_from_links,
    select_boards,
    structured_job_links,
    x_handle_from_links,
)
from tests.conftest import FIXTURES

WEB = FIXTURES / "web"
BASE = "https://acme-robotics.com"
PUBLIC_IP = "93.184.216.34"


def _html(name: str) -> str:
    return (WEB / name).read_text()


@pytest.fixture()
def public_dns(monkeypatch):
    """Every hostname resolves to a public address (tests never hit real DNS)."""

    def fake(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.fixture()
def provider(thesis):
    return WebsiteProvider(client=HttpClient(guard_ssrf=True, max_retries=0), thesis=thesis)


def _mock_site(careers: str = "acme_careers.html") -> None:
    html = {"Content-Type": "text/html; charset=utf-8"}
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(200, text=(WEB / "robots.txt").read_text()))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(200, text=_html("acme_home.html"), headers=html))
    respx.get(f"{BASE}/about").mock(return_value=httpx.Response(200, text=_html("acme_about.html"), headers=html))
    respx.get(f"{BASE}/careers").mock(return_value=httpx.Response(200, text=_html(careers), headers=html))
    respx.get(f"{BASE}/blog/").mock(return_value=httpx.Response(200, text="<title>Blog</title><p>Posts</p>",
                                                               headers=html))
    respx.get("https://docs.acme-robotics.com/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://docs.acme-robotics.com/").mock(
        return_value=httpx.Response(200, text="<title>Docs</title><p>API</p>", headers=html))


# ── extraction ──
def test_extract_page_fields_and_untrusted_content_dropped():
    page = extract_page(_html("acme_home.html"), f"{BASE}/")
    assert page.title == "Home | Acme Robotics"
    assert page.description == "Acme Robotics builds dexterous manipulation systems for factories."
    assert page.site_name == "Acme Robotics"
    assert "Robots that work with their hands" in page.text
    assert "SECRET_SCRIPT" not in page.text and "evil.example" not in page.text  # script dropped
    assert "FOOTER_ONLY_TEXT" not in page.text and "Press kit" not in page.text  # footer + nav dropped
    urls = {link.url for link in page.links}
    assert f"{BASE}/about" in urls and "https://github.com/acme-robotics" in urls
    assert not any(u.startswith(("mailto:", "#")) for u in urls)


@pytest.mark.parametrize(
    "url, anchor, kind",
    [
        (f"{BASE}/careers", None, "careers"),
        (f"{BASE}/en/about-us", None, "about"),
        (f"{BASE}/team/", None, "team"),
        ("https://docs.acme-robotics.com/", None, "docs"),
        ("https://careers.acme-robotics.com", None, "careers"),
        (f"{BASE}/publications.html", None, "research"),
        (f"{BASE}/p/xyz", "We’re hiring", "careers"),
        (f"{BASE}/p/abc", "Our story", "about"),
        (f"{BASE}/pricing", "Pricing", None),
    ],
)
def test_classify_page_kind(url, anchor, kind):
    assert classify_page_kind(url, anchor) == kind


def test_discover_pages_same_registrable_domain_shallowest():
    page = extract_page(_html("acme_home.html"), f"{BASE}/")
    targets = discover_pages(page, "acme-robotics.com")
    assert targets == {
        "about": f"{BASE}/about",
        "team": f"{BASE}/team",
        "careers": f"{BASE}/careers",
        "blog": f"{BASE}/blog/",  # index beats /blog/2026/first-post
        "docs": "https://docs.acme-robotics.com/",
    }
    assert all("other-company.com" not in u for u in targets.values())


def test_identity_links_org_level_only():
    page = extract_page(_html("acme_home.html"), f"{BASE}/")
    anchors = ["acme-robotics", "Acme Robotics"]
    assert github_org_from_links(page.links, anchors) == "acme-robotics"  # not 'pytorch' (repo link)
    assert x_handle_from_links(page.links, anchors) == "acmerobotics"  # not 'intent'


def test_identity_links_ambiguous_or_unrelated_rejected():
    links = [Link("https://github.com/pytorch", "PyTorch", False), Link("https://github.com/numpy", "NumPy", False)]
    assert github_org_from_links(links, ["acme", "Acme"]) is None
    # A single unrelated org in body copy is not an identity link; in site chrome it is.
    assert github_org_from_links([Link("https://github.com/acmehq", "", False)], ["zeta", "Zeta"]) is None
    assert github_org_from_links([Link("https://github.com/acmehq", "", True)], ["zeta", "Zeta"]) == "acmehq"
    assert x_handle_from_links([Link("https://x.com/home", "", True)], ["acme"]) is None


def test_structured_job_links_filters_non_jobs_and_other_domains():
    page = extract_page(_html("acme_careers.html"), f"{BASE}/careers")
    assert structured_job_links(page, "acme-robotics.com") == [
        (f"{BASE}/careers/senior-robotics-engineer", "Senior Robotics Engineer"),
        (f"{BASE}/careers/research-scientist-ml", "Research Scientist, Machine Learning"),
        (f"{BASE}/careers/account-executive", "Account Executive"),
    ]


def test_select_boards():
    anchors = ["acme-robotics", "Acme Robotics"]
    assert select_boards([("ashby", "acme-robotics")], anchors) == ([("ashby", "acme-robotics")], None)
    chosen, err = select_boards([("lever", "zeta"), ("ashby", "acme"), ("greenhouse", "omega")], anchors)
    assert chosen == [("ashby", "acme")] and err is None
    chosen, err = select_boards([("lever", "zeta"), ("greenhouse", "omega")], anchors)
    assert chosen == [] and "not attributing" in err


# ── formation phrases ──
@pytest.mark.parametrize(
    "text, expected",
    [
        ("We’re hiring a founding engineer!", ["we're hiring", "founding engineer"]),
        ("we are hiring founding engineers", ["we're hiring", "founding engineer"]),
        ("Excited to share what we’ve been building", ["excited to share what we've been building"]),
        ("Currently in STEALTH.", ["stealth"]),
        ("Co-founder at Acme; prev cofounder of Beta", ["co-founder", "cofounder"]),
        ("stealthy ninja moves", []),
        ("The relaunching of the product", []),
        ("Day 1 at the new company", ["day 1 at"]),
        ("", []),
    ],
)
def test_detect_formation_phrases(thesis, text, expected):
    assert detect_formation_phrases(text, thesis) == expected


# ── full crawl ──
@respx.mock
def test_crawl_structured_jobs(provider, public_dns):
    _mock_site()
    batch = provider.crawl("acme-robotics.com", max_pages=6)

    (company,) = batch.companies
    assert company.ref.key == "domain:acme-robotics.com"
    assert company.name == "Acme Robotics"
    assert company.website == BASE
    assert company.github_org == "acme-robotics" and company.x_handle == "acmerobotics"
    assert {r.key for r in company.alt_refs} == {"github_org:acme-robotics", "x:acmerobotics"}

    assert any("robots.txt disallows" in e and "/team" in e for e in batch.errors)
    kinds = {s["page_kind"]: s for s in batch.sources}
    assert set(kinds) == {"home", "about", "careers", "blog", "docs"}
    home = kinds["home"]
    assert home["source_type"] == "website" and home["company_ref"] == "domain:acme-robotics.com"
    assert len(home["content_hash"]) == 64 and len(home["snippet"]) <= 300

    assert [j.title for j in batch.jobs] == [
        "Senior Robotics Engineer", "Research Scientist, Machine Learning", "Account Executive"
    ]
    assert all(j.external_id == j.url and j.ats == "website" for j in batch.jobs)
    assert batch.metrics[0].metric == "open_roles" and batch.metrics[0].value == 3

    signals = {s.signal_type: s for s in batch.signals}
    assert set(signals) == {"hiring_active", "formation_language"}
    hiring = signals["hiring_active"]
    assert hiring.raw_value == 2 and hiring.metadata["role_classes"] == {"robotics_controls": 1, "ml_research": 1}
    formation = signals["formation_language"]
    assert formation.epistemic == "FACT"
    # 'we're hiring' and 'co-founder' are not formation evidence on a company site.
    assert formation.metadata["matched_phrases"] == ["stealth", "founding engineer", "building something new"]
    assert all(q["quote"] and q["url"].startswith(BASE) for q in formation.metadata["quotes"])


@respx.mock
def test_crawl_ats_board_preferred_over_links(provider, public_dns):
    _mock_site("acme_careers_ashby.html")
    ashby = respx.get("https://api.ashbyhq.com/posting-api/job-board/acme-robotics").mock(
        return_value=httpx.Response(200, json=json.loads((FIXTURES / "ats" / "ashby_board.json").read_text())))
    batch = provider.crawl(BASE, max_pages=3)
    assert ashby.called
    assert [j.ats for j in batch.jobs] == ["ashby", "ashby"]
    hiring = next(s for s in batch.signals if s.signal_type == "hiring_active")
    assert hiring.raw_value == 2 and hiring.source_url == "https://jobs.ashbyhq.com/acme-robotics"
    assert len(batch.sources) == 3  # max_pages counts the homepage


@respx.mock
def test_crawl_without_job_source_emits_no_hiring_facts(provider, public_dns):
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(
        200, text="<title>Acme Robotics</title><p>Hands for robots.</p>", headers={"Content-Type": "text/html"}))
    batch = provider.crawl(BASE)
    assert batch.companies[0].name == "Acme Robotics"
    assert batch.jobs == [] and batch.metrics == [] and batch.signals == []


@respx.mock
def test_crawl_bot_challenge_page_not_parsed(provider, public_dns):
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(
        200, text="<title>Vercel Security Checkpoint</title>", headers={"Content-Type": "text/html"}))
    batch = provider.crawl(BASE)
    assert batch.companies == [] and "bot-challenge" in batch.errors[0]


@respx.mock
def test_crawl_robots_disallows_homepage(provider, public_dns):
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /"))
    home = respx.get(f"{BASE}/")
    batch = provider.crawl(BASE)
    assert not home.called and batch.companies == [] and "robots.txt disallows" in batch.errors[0]


@respx.mock
def test_crawl_redirect_to_private_ip_blocked(provider, monkeypatch):
    def fake(host, *a, **k):
        ip = "10.0.0.7" if host == "intranet.acme-robotics.com" else PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    respx.get(f"{BASE}/robots.txt").mock(return_value=httpx.Response(404))
    respx.get(f"{BASE}/").mock(return_value=httpx.Response(302, headers={"Location": "https://intranet.acme-robotics.com/"}))
    internal = respx.get("https://intranet.acme-robotics.com/")
    batch = provider.crawl(BASE)
    assert not internal.called
    assert batch.companies == [] and "blocked unsafe URL" in batch.errors[0]


def test_crawl_rejects_unsafe_input(provider):
    with pytest.raises(UnsafeURLError):
        provider.crawl("http://localhost:8080/admin")
    with pytest.raises(UnsafeURLError):
        provider.crawl("http://127.0.0.1/")


def test_crawl_platform_url_is_not_a_company(provider, public_dns):
    batch = provider.crawl("https://github.com/acme-robotics")
    assert batch.companies == [] and "not a company domain" in batch.errors[0]


@respx.mock
def test_truncated_board_marks_jobs_incomplete(provider, public_dns, monkeypatch):
    from radar.providers import ats as ats_mod

    _mock_site("acme_careers_ashby.html")
    real = ats_mod.fetch_board

    def truncated(client, ats, slug, company):
        return ats_mod.BoardResult(real(client, ats, slug, company).jobs[:1], False)

    respx.get("https://api.ashbyhq.com/posting-api/job-board/acme-robotics").mock(
        return_value=httpx.Response(200, json=json.loads((FIXTURES / "ats" / "ashby_board.json").read_text())))
    monkeypatch.setattr(ats_mod, "fetch_board", truncated)
    batch = provider.crawl(BASE, max_pages=3)
    assert len(batch.jobs) == 1 and batch.jobs[0].complete is False
    assert not [m for m in batch.metrics if m.metric == "open_roles"]
    assert any("truncated" in e for e in batch.errors)

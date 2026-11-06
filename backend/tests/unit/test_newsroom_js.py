"""Newsrooms rendered by JavaScript frameworks (hand audit #10): visible dates, CMS attributes, embedded page
state, JSON-LD lists without urls, sitemaps and post pages. Fixtures are modelled on the patterns seen on the
portfolio sites; nothing is fetched live."""

from __future__ import annotations

import socket
from datetime import UTC, datetime

import httpx
import pytest
import respx

from radar.providers.base import domain_ref
from radar.providers.http import HttpClient
from radar.providers.newsroom import (
    extract_items,
    is_post_url,
    parse_sitemap,
    post_links,
    post_page_item,
)
from radar.providers.website import WebsiteProvider
from tests.conftest import FIXTURES

D = FIXTURES / "newsroom"
NOW = datetime(2026, 11, 6, 18, tzinfo=UTC)
SITE = "https://www.acme-robotics.com"


def _read(n: str) -> str:
    return (D / n).read_text()


def _items(name: str, page: str):
    return {i.url.removeprefix(SITE): (i.title, i.published_at.date().isoformat(), i.via)
            for i in extract_items(_read(name), f"{SITE}{page}", now=NOW)}


def test_visible_date_cards():
    got = _items("js_visible_dates.html", "/blog")
    assert got == {
        "/blog/api-beta": ("Frontier Models for Every Robot", "2026-10-13", "visible_date"),
        "/progress/from-zero-to-one": ("From Zero to One", "2026-08-18", "visible_date"),
        "/posts/servo-hacking": ("The $15 Servo Rabbit Hole", "2026-09-16", "visible_date"),
        "/field-notes/continual-grading": ("Continual Grading", "2026-11-02", "visible_date"),
    }  # page header/footer dates, a two-link container and off-site press are never items


def test_numeric_dates_only_when_the_page_settles_the_order():
    got = _items("js_numeric_dates.html", "/updates")
    assert got["/blog-posts/railroad"][1] == "2025-12-04"  # 25.11.2025 proves day.month.year
    assert got["/blog-posts/spacenews"][1] == "2025-11-25"
    assert _items("js_numeric_ambiguous.html", "/updates") == {}  # 04.12 vs 12.04: never guessed


def test_webflow_cms_attributes_and_jsonld_list_without_urls():
    got = _items("webflow_cms.html", "/newsroom")
    assert got["/newsroom/acme-signs-loi"] == ("Acme Signs Letter of Intent For $175 Million CHIPS Award",
                                               "2026-10-22", "cms_attr")
    assert got["/newsroom/acme-achieves-new-gripper-milestones"][1:] == ("2026-02-13", "jsonld")
    assert len(got) == 2  # the newspaper story links off-site: not a company post


def test_next_data_state_is_parsed_not_executed():
    got = _items("next_data.html", "/blog")
    assert got == {"/blog/gelgrip-3": ("Introducing Gelgrip 3", "2026-10-30", "next_data"),
                   "/blog/hiring-plan": ("Our hiring plan", "2026-10-12", "next_data")}


def test_post_pages():
    a = post_page_item(_read("post_meta.html"), f"{SITE}/newsroom/r2-hand", now=NOW)
    assert (a.title, a.published_at.date().isoformat(), a.via, a.date_basis) == (
        "Acme unveils the R2 humanoid hand", "2026-10-21", "post_page", "published")
    b = post_page_item(_read("post_visible.html"), f"{SITE}/newsroom/army-contract", now=NOW)
    assert (b.title, b.published_at.date().isoformat()) == ("Acme wins Army laboratory contract", "2026-10-09")
    assert post_page_item(_read("post_undated.html"), f"{SITE}/newsroom/approach", now=NOW) is None
    lm = datetime(2026, 9, 2, tzinfo=UTC)
    c = post_page_item(_read("post_undated.html"), f"{SITE}/newsroom/approach", now=NOW, lastmod=lm)
    assert (c.via, c.date_basis, c.published_at) == ("sitemap_lastmod", "sitemap_lastmod", lm)


def test_sitemaps_and_post_urls():
    urls, children = parse_sitemap(_read("sitemap_index.xml"), f"{SITE}/sitemap.xml")
    assert urls == [] and children == [f"{SITE}/sitemap-pages.xml", f"{SITE}/sitemap-posts.xml"]  # off-site dropped
    urls, _ = parse_sitemap(_read("sitemap_posts.xml"), f"{SITE}/sitemap-posts.xml")
    posts = [u for u, _ in urls if is_post_url(u, f"{SITE}/newsroom")]
    assert posts == [f"{SITE}/newsroom/r2-hand", f"{SITE}/newsroom/army-contract", f"{SITE}/newsroom/approach"]
    assert parse_sitemap('<!DOCTYPE x [<!ENTITY a "b">]><urlset/>', f"{SITE}/sitemap.xml") == ([], [])
    html = '<nav><a href="/newsroom/nav-item">N</a></nav><a href="/newsroom/one">1</a><a href="/about">A</a>'
    assert post_links(html, f"{SITE}/newsroom") == [f"{SITE}/newsroom/one"]


@pytest.fixture()
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))])


HTML = {"Content-Type": "text/html; charset=utf-8"}
XML = {"Content-Type": "application/xml"}


@respx.mock
def test_crawl_discovers_posts_from_sitemaps_on_the_redirect_domain(thesis, public_dns, monkeypatch):
    """A JS-rendered newsroom with no dates in its HTML: posts come from the sitemap and their own pages; the
    configured identity (acme.ai) owns every record although the site redirects to www.acme-robotics.com."""
    from radar.settings import get_settings

    monkeypatch.setattr(get_settings(), "today_override", "2026-11-06")
    respx.get("https://acme.ai/robots.txt").mock(return_value=httpx.Response(404))
    respx.get("https://acme.ai/").mock(return_value=httpx.Response(301, headers={"Location": f"{SITE}/"}))
    respx.get(f"{SITE}/robots.txt").mock(return_value=httpx.Response(
        200, text=f"User-agent: *\nAllow: /\nSitemap: {SITE}/sitemap.xml\n"))
    respx.get(f"{SITE}/").mock(return_value=httpx.Response(200, headers=HTML, text=(
        '<html><head><title>Acme</title></head><body><nav><a href="/newsroom">Newsroom</a></nav></body></html>')))
    respx.get(f"{SITE}/newsroom").mock(return_value=httpx.Response(200, headers=HTML, text=(
        '<html><body><div id="root"></div><a href="/newsroom/approach">Our approach</a></body></html>')))
    respx.get(f"{SITE}/sitemap.xml").mock(return_value=httpx.Response(200, headers=XML,
                                                                      text=_read("sitemap_index.xml")))
    respx.get(f"{SITE}/sitemap-posts.xml").mock(return_value=httpx.Response(200, headers=XML,
                                                                            text=_read("sitemap_posts.xml")))
    respx.get(f"{SITE}/sitemap-pages.xml").mock(return_value=httpx.Response(200, headers=XML,
                                                                            text=_read("sitemap_pages.xml")))
    evil = respx.get("https://evil.example.org/sitemap.xml").mock(return_value=httpx.Response(200))
    respx.get(f"{SITE}/newsroom/r2-hand").mock(return_value=httpx.Response(200, headers=HTML,
                                                                           text=_read("post_meta.html")))
    respx.get(f"{SITE}/newsroom/army-contract").mock(return_value=httpx.Response(200, headers=HTML,
                                                                                 text=_read("post_visible.html")))
    respx.get(f"{SITE}/newsroom/approach").mock(return_value=httpx.Response(200, headers=HTML,
                                                                            text=_read("post_undated.html")))
    web = WebsiteProvider(client=HttpClient(guard_ssrf=True, max_retries=0), thesis=thesis)
    batch = web.crawl("https://acme.ai", max_pages=3, priority=("news",), newsroom=True,
                      entity=domain_ref("acme.ai"))
    posts = {s.source_url.removeprefix(SITE): s for s in batch.signals if s.signal_type == "company_post"}
    assert set(posts) == {"/newsroom/r2-hand", "/newsroom/army-contract", "/newsroom/approach"}
    assert posts["/newsroom/approach"].metadata["date_basis"] == "sitemap_lastmod"
    assert posts["/newsroom/r2-hand"].metadata["date_basis"] == "published"
    assert all(s.entity.key == "domain:acme.ai" for s in batch.signals)
    company = batch.companies[0]
    assert company.ref.key == "domain:acme.ai" and "domain:acme-robotics.com" in {r.key for r in company.alt_refs}
    assert not evil.called


def test_weekday_prefixed_date_on_a_post_page():
    html = ('<head><meta property="og:title" content="Army Applications Laboratory Selects Acme | Acme"></head>'
            '<main><div><p><a href="/news">Article</a></p><h1>Army Applications Laboratory Selects Acme</h1>'
            '<p>Tuesday, April 22 2025</p></div></main>')
    item = post_page_item(html, f"{SITE}/news/aal", now=NOW)
    assert item.published_at.date().isoformat() == "2025-04-22"
    assert item.title == "Army Applications Laboratory Selects Acme"  # site suffix dropped

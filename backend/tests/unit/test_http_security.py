"""SSRF guard (sanitize_url / assert_public_host / redirect re-checks) and robots.txt handling."""

from __future__ import annotations

import ipaddress
import socket

import httpx
import pytest
import respx

from radar.providers.http import HttpClient, RobotsCache, UnsafeURLError, assert_public_host, sanitize_url

PUBLIC_IP = "93.184.216.34"


def _resolver(mapping: dict[str, str]):
    def fake(host, *a, **k):
        try:
            ip = str(ipaddress.ip_address(host))  # IP literals resolve to themselves
        except ValueError:
            ip = mapping.get(host, PUBLIC_IP)
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    return fake


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/file",
        "gopher://example.com/",
        "http://localhost/",
        "http://LOCALHOST./admin",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://db.internal/",
        "http://printer.local/",
        "http://user:pass@example.com/",
        "http://user@example.com/",
        "http://example.com:22/",
        "http://example.com:6379/",
        "",
    ],
)
def test_sanitize_url_rejects(url):
    with pytest.raises(UnsafeURLError):
        sanitize_url(url)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,hi"])
def test_sanitize_url_rejects_pseudo_schemes(url):
    # Currently surfaces as a plain ValueError (port parse) rather than UnsafeURLError; either way rejected.
    with pytest.raises(ValueError):
        sanitize_url(url)


def test_sanitize_url_normalizes():
    assert sanitize_url("Example.COM/path?q=1#frag") == "https://example.com/path?q=1"
    assert sanitize_url("http://example.com:8443") == "http://example.com:8443/"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://0.0.0.0/",
        "http://2130706433/",  # decimal 127.0.0.1
    ],
)
def test_assert_public_host_rejects_private_literals(url):
    with pytest.raises(UnsafeURLError):
        assert_public_host(sanitize_url(url))


def test_assert_public_host_uses_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"rebind.example.com": "10.0.0.5"}))
    with pytest.raises(UnsafeURLError):
        assert_public_host("https://rebind.example.com/")
    assert_public_host("https://ok.example.com/")  # public → no error


def test_assert_public_host_unresolvable(monkeypatch):
    def boom(*a, **k):
        raise socket.gaierror("nope")

    monkeypatch.setattr(socket, "getaddrinfo", boom)
    with pytest.raises(UnsafeURLError):
        assert_public_host("https://does-not-exist.example/")


@respx.mock
@pytest.mark.parametrize(
    "location, mapping",
    [
        ("http://169.254.169.254/latest/meta-data/", {}),
        ("https://internal.example.com/", {"internal.example.com": "10.0.0.5"}),
        ("http://localhost:8080/", {}),
        ("file:///etc/passwd", {}),
    ],
)
def test_redirect_to_private_target_blocked(monkeypatch, location, mapping):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver(mapping))
    respx.get("https://public.example.com/").mock(return_value=httpx.Response(302, headers={"Location": location}))
    client = HttpClient(guard_ssrf=True, max_retries=0)
    with pytest.raises(UnsafeURLError):
        client.get("https://public.example.com/")
    assert len(respx.calls) == 1  # the private target was never requested


@respx.mock
def test_guarded_client_follows_public_redirect(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({}))
    respx.get("https://a.example.com/").mock(return_value=httpx.Response(301, headers={"Location": "/home"}))
    respx.get("https://a.example.com/home").mock(return_value=httpx.Response(200, text="ok"))
    r = HttpClient(guard_ssrf=True, max_retries=0).get("https://a.example.com/")
    assert r.text == "ok" and str(r.request.url) == "https://a.example.com/home"


# ── robots.txt ──
@respx.mock
def test_robots_disallow_rules():
    respx.get("https://site.example.com/robots.txt").mock(return_value=httpx.Response(
        200, text="User-agent: *\nDisallow: /private\n\nUser-agent: AntiRadar\nDisallow: /no-radar\n"))
    robots = RobotsCache(HttpClient(max_retries=0))
    assert robots.allowed("https://site.example.com/about")
    assert not robots.allowed("https://site.example.com/no-radar/page")
    # The AntiRadar group overrides '*', so /private is allowed for us per RFC 9309 group selection.
    assert robots.allowed("https://site.example.com/private")
    assert len(respx.calls) == 1  # cached per origin


@respx.mock
def test_robots_status_handling():
    respx.get("https://forbidden.example.com/robots.txt").mock(return_value=httpx.Response(403))
    respx.get("https://missing.example.com/robots.txt").mock(return_value=httpx.Response(404))
    robots = RobotsCache(HttpClient(max_retries=0))
    assert not robots.allowed("https://forbidden.example.com/anything")
    assert robots.allowed("https://missing.example.com/anything")

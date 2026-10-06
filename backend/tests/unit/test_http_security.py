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


# ── is_global semantics (CGNAT, embedded IPv4) ──
@pytest.mark.parametrize(
    "ip",
    [
        "100.100.100.200",  # Alibaba Cloud metadata (CGNAT 100.64/10)
        "100.64.1.1",
        "198.18.0.1",  # benchmarking
        "192.0.2.1",  # documentation
        "240.0.0.1",
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "2002:7f00:1::",  # 6to4 wrapping 127.0.0.1
        "2002:a9fe:a9fe::1",  # 6to4 wrapping 169.254.169.254
        "64:ff9b::a9fe:a9fe",  # NAT64 of 169.254.169.254
        "64:ff9b::7f00:1",  # NAT64 of 127.0.0.1
        "64:ff9b:1::a00:1",  # local-use NAT64 of 10.0.0.1
        "::127.0.0.1",  # IPv4-compatible loopback
        "2001:db8::1",
        "fc00::1",
        "fe80::1%eth0",
        "ff0e::1",
        "not-an-ip",
    ],
)
def test_is_public_ip_rejects_non_global(ip):
    from radar.providers.http import _is_public_ip

    assert not _is_public_ip(ip)


@pytest.mark.parametrize("ip", [PUBLIC_IP, "8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"])
def test_is_public_ip_accepts_global(ip):
    from radar.providers.http import _is_public_ip

    assert _is_public_ip(ip)


def test_cgnat_metadata_literal_blocked():
    with pytest.raises(UnsafeURLError):
        assert_public_host(sanitize_url("http://100.100.100.200/latest/meta-data/"))


def test_hostname_resolving_into_cgnat_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"k8s.example.com": "100.70.0.5"}))
    with pytest.raises(UnsafeURLError):
        assert_public_host("https://k8s.example.com/")


# ── DNS rebinding: resolve once, connect to the validated IP ──
def test_rebinding_between_check_and_connect_is_blocked(monkeypatch):
    """First lookup (pre-check) is public, every later lookup is loopback: the connect must be refused."""
    calls = {"n": 0}

    def flip(host, *a, **k):
        calls["n"] += 1
        ip = PUBLIC_IP if calls["n"] == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", flip)
    client = HttpClient(guard_ssrf=True, max_retries=0)
    with pytest.raises(UnsafeURLError):
        client.get("http://rebind.example.com:8080/")
    assert calls["n"] >= 2


def test_guarded_backend_connects_to_validated_ip(monkeypatch):
    from radar.providers.http import _RadarNetworkBackend

    monkeypatch.setattr(socket, "getaddrinfo", _resolver({"pinned.example.com": PUBLIC_IP}))
    seen = {}

    class FakeSock:
        def getpeername(self):
            return (PUBLIC_IP, 443)

    class FakeStream:
        def get_extra_info(self, info):
            return FakeSock() if info == "socket" else None

        def close(self):
            pass

    backend = _RadarNetworkBackend(guard_ssrf=True)

    def connect(host, port, *a, **k):
        seen["host"] = host
        return FakeStream()

    monkeypatch.setattr(backend._inner, "connect_tcp", connect)
    backend.connect_tcp("pinned.example.com", 443, timeout=1.0)
    assert seen["host"] == PUBLIC_IP  # the literal IP, never the hostname (no second resolution)


def test_guarded_backend_rejects_private_peer(monkeypatch):
    from radar.providers.http import _RadarNetworkBackend

    monkeypatch.setattr(socket, "getaddrinfo", _resolver({}))
    closed = {}

    class FakeSock:
        def getpeername(self):
            return ("10.0.0.7", 443)

    class FakeStream:
        def get_extra_info(self, info):
            return FakeSock()

        def close(self):
            closed["yes"] = True

    backend = _RadarNetworkBackend(guard_ssrf=True)
    monkeypatch.setattr(backend._inner, "connect_tcp", lambda *a, **k: FakeStream())
    with pytest.raises(UnsafeURLError):
        backend.connect_tcp("odd.example.com", 443)
    assert closed


def test_guarded_backend_rejects_if_any_address_private(monkeypatch):
    from radar.providers.http import _RadarNetworkBackend

    def mixed(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0)),
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", mixed)
    with pytest.raises(UnsafeURLError):
        _RadarNetworkBackend(guard_ssrf=True).connect_tcp("mixed.example.com", 80)


@respx.mock
def test_retry_revalidates_host(monkeypatch):
    calls = {"n": 0}

    def flip(host, *a, **k):
        calls["n"] += 1
        ip = PUBLIC_IP if calls["n"] == 1 else "10.0.0.9"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(socket, "getaddrinfo", flip)
    monkeypatch.setattr("radar.providers.http.time.sleep", lambda s: None)
    respx.get("https://flaky.example.com/").mock(return_value=httpx.Response(503))
    with pytest.raises(UnsafeURLError):
        HttpClient(guard_ssrf=True, max_retries=2).get("https://flaky.example.com/")
    assert len(respx.calls) == 1


# ── wall-clock deadlines ──
def _drip_server(drip_headers: bool):
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(4)
    stop = threading.Event()

    def serve():
        srv.settimeout(5)
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        with conn:
            conn.recv(4096)
            try:
                if drip_headers:
                    for ch in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 1000:
                        if stop.wait(0.05):
                            return
                        conn.sendall(bytes([ch]))
                else:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 100000\r\n\r\n")
                    for _ in range(1000):
                        if stop.wait(0.05):
                            return
                        conn.sendall(b"x")
            except OSError:
                return

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    return srv, stop


@pytest.mark.parametrize("drip_headers", [False, True])
def test_drip_feed_server_hits_wall_clock_deadline(drip_headers):
    import time as _time

    from radar.providers.http import DeadlineExceeded

    srv, stop = _drip_server(drip_headers)
    port = srv.getsockname()[1]
    client = HttpClient(timeout=0.5, attempt_deadline=1.0, max_retries=2)
    t0 = _time.monotonic()
    try:
        with pytest.raises(DeadlineExceeded):
            client.get(f"http://127.0.0.1:{port}/")
        assert _time.monotonic() - t0 < 3.0  # per-op timeout never fires; the deadline does, without retries
    finally:
        stop.set()
        srv.close()
        client.close()


@respx.mock
def test_job_deadline_stops_further_requests():
    from radar.providers.http import DeadlineExceeded

    respx.get("https://a.example.com/").mock(return_value=httpx.Response(200, text="ok"))
    client = HttpClient(max_retries=0)
    with client.deadline(60):
        assert client.get("https://a.example.com/").text == "ok"
    with client.deadline(0), pytest.raises(DeadlineExceeded):
        client.get("https://a.example.com/")
    assert len(respx.calls) == 1
    assert client.get("https://a.example.com/").text == "ok"  # deadline restored after the block


@respx.mock
def test_job_deadline_cuts_retry_backoff(monkeypatch):
    from radar.providers.http import DeadlineExceeded

    slept = []
    monkeypatch.setattr("radar.providers.http.time.sleep", lambda s: slept.append(s))
    respx.get("https://busy.example.com/").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "60"}))
    client = HttpClient(max_retries=3)
    client.set_deadline(5)
    with pytest.raises(DeadlineExceeded):
        client.get("https://busy.example.com/")
    assert slept == []


# ── credentials on redirects ──
@respx.mock
def test_cross_origin_redirect_drops_credentials():
    respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(302, headers={"Location": "https://evil.example/collect"}))
    respx.get("https://evil.example/collect").mock(
        return_value=httpx.Response(302, headers={"Location": "https://api.github.com/back"}))
    respx.get("https://api.github.com/back").mock(return_value=httpx.Response(200, text="ok"))
    client = HttpClient(headers={"Authorization": "Bearer T"}, max_retries=0)
    client.get("https://api.github.com/repos/a/b", headers={"Cookie": "s=1", "X-Api-Key": "k"})
    first, second, third = (c.request for c in respx.calls)
    assert first.headers["authorization"] == "Bearer T" and first.headers["cookie"] == "s=1"
    for req in (second, third):  # stays stripped even when the chain returns to the original origin
        assert "authorization" not in req.headers
        assert "cookie" not in req.headers and "x-api-key" not in req.headers
    assert second.headers["user-agent"]  # non-credential defaults still sent


@respx.mock
def test_same_origin_redirect_keeps_credentials():
    respx.get("https://api.github.com/repos/a/b").mock(
        return_value=httpx.Response(301, headers={"Location": "/repositories/1"}))
    respx.get("https://api.github.com/repositories/1").mock(return_value=httpx.Response(200, text="ok"))
    HttpClient(headers={"Authorization": "Bearer T"}, max_retries=0).get("https://api.github.com/repos/a/b")
    assert respx.calls[1].request.headers["authorization"] == "Bearer T"


@respx.mock
def test_scheme_or_port_change_counts_as_cross_origin():
    respx.get("https://api.example.com/").mock(
        return_value=httpx.Response(302, headers={"Location": "http://api.example.com/"}))
    respx.get("http://api.example.com/").mock(return_value=httpx.Response(200, text="ok"))
    HttpClient(headers={"Authorization": "Bearer T"}, max_retries=0).get("https://api.example.com/")
    assert "authorization" not in respx.calls[1].request.headers

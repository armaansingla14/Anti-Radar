"""Hardened HTTP layer shared by every provider.

* SSRF guard for user-supplied URLs: http(s) only, no credentials in URL,
  DNS-resolved address must be globally routable; re-checked on every redirect
  and retry, and enforced again at connect time: the guarded network backend
  resolves once, validates every address and connects to the validated IP (so a
  DNS-rebinding answer between check and connect cannot reach a private host).
* Wall-clock deadlines: every attempt is bounded (connect + headers + body), and
  callers can set an overall deadline for a multi-request job (crawl/investigation).
* Credential headers (Authorization, Cookie, ...) are dropped on cross-origin redirects.
* Per-host politeness (minimum interval between requests) + robots.txt for crawling.
* Retries with backoff on 429/5xx; honours Retry-After.
* Per-run stats (requests, latency, rate-limit remaining) for observability.
* Record / replay cassettes so provider parsing is testable offline and the
  demo snapshot can be captured from real APIs.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import ipaddress
import json
import socket
import statistics
import threading
import time
import urllib.robotparser
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpcore
import httpx

from radar.settings import get_settings

MAX_BODY_BYTES = 3 * 1024 * 1024
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}


# Headers that carry credentials; never forwarded to a different origin on redirect.
CREDENTIAL_HEADERS = frozenset({
    "authorization", "proxy-authorization", "cookie", "x-api-key", "api-key", "x-auth-token",
})


class UnsafeURLError(ValueError):
    pass


class DeadlineExceeded(httpx.TimeoutException):
    """The wall-clock budget for a request attempt or an overall job ran out. Never retried."""

    def __init__(self, message: str = "wall-clock deadline exceeded") -> None:
        super().__init__(message)


def sanitize_url(url: str) -> str:
    """Normalize and validate a user-supplied URL. Raises UnsafeURLError."""
    url = (url or "").strip()
    if not url:
        raise UnsafeURLError("empty URL")
    if "://" not in url:
        url = "https://" + url
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise UnsafeURLError(f"scheme not allowed: {p.scheme}")
    try:
        port = p.port
    except ValueError as e:
        raise UnsafeURLError("invalid port") from e
    if p.username or p.password:
        raise UnsafeURLError("credentials in URL are not allowed")
    host = (p.hostname or "").lower().rstrip(".")
    if not host or host in BLOCKED_HOSTNAMES or host.endswith(".local") or host.endswith(".internal"):
        raise UnsafeURLError(f"host not allowed: {host!r}")
    if port not in (None, 80, 443, 8080, 8443):
        raise UnsafeURLError(f"port not allowed: {port}")
    # Drop fragments; keep path + query.
    return urlunparse((p.scheme, p.netloc.lower(), p.path or "/", "", p.query, ""))


# NAT64 prefixes (RFC 6052 well-known + RFC 8215 local-use): the low 32 bits are an IPv4 address.
_NAT64_NETS = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))


def _embedded_ipv4(addr: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """IPv4 addresses an IPv6 address tunnels/translates to (mapped, 6to4, Teredo, NAT64, compatible)."""
    out: list[ipaddress.IPv4Address] = []
    if addr.ipv4_mapped:
        out.append(addr.ipv4_mapped)
    if addr.sixtofour:
        out.append(addr.sixtofour)
    if addr.teredo:
        out.extend(addr.teredo)
    if any(addr in n for n in _NAT64_NETS):
        out.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    if int(addr) >> 32 == 0:  # deprecated IPv4-compatible ::a.b.c.d
        out.append(ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF))
    return out


def _is_public_ip(ip: str) -> bool:
    """True only for globally routable unicast addresses (is_global semantics).

    Excludes private, loopback, link-local, CGNAT (100.64/10, e.g. Alibaba's 100.100.100.200 metadata),
    benchmarking, documentation, reserved and multicast ranges; IPv6 addresses that embed an IPv4
    address are judged by the embedded address as well.
    """
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])  # drop an IPv6 zone id
    except ValueError:
        return False
    if not addr.is_global or addr.is_multicast or addr.is_unspecified:
        return False
    if isinstance(addr, ipaddress.IPv6Address):
        return all(_is_public_ip(str(v4)) for v4 in _embedded_ipv4(addr))
    return True


def resolve_public(host: str, port: int | None = None) -> list[str]:
    """Resolve ``host`` once and return its addresses; raise UnsafeURLError if ANY is non-public."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise UnsafeURLError(f"cannot resolve {host}") from e
    ips = list(dict.fromkeys(str(info[4][0]) for info in infos))
    if not ips:
        raise UnsafeURLError(f"cannot resolve {host}")
    for ip in ips:
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"{host} resolves to non-public address {ip}")
    return ips


def assert_public_host(url: str) -> None:
    resolve_public(urlparse(url).hostname or "")


# ── network layer: connect-time SSRF enforcement + wall-clock deadlines ──

# Absolute time.monotonic() deadline for the current request attempt (per thread/context).
_attempt_deadline: contextvars.ContextVar[float | None] = contextvars.ContextVar("radar_http_deadline", default=None)


def _bounded(timeout: float | None) -> float | None:
    """Clamp a per-operation socket timeout to the remaining wall-clock budget."""
    deadline = _attempt_deadline.get()
    if deadline is None:
        return timeout
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise DeadlineExceeded()
    return remaining if timeout is None else min(timeout, remaining)


class _DeadlineStream(httpcore.NetworkStream):
    """Wraps a stream so every read/write (headers included) honours the attempt deadline."""

    def __init__(self, inner: httpcore.NetworkStream) -> None:
        self._inner = inner

    def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return self._inner.read(max_bytes, _bounded(timeout))

    def write(self, buffer: bytes, timeout: float | None = None) -> None:
        self._inner.write(buffer, _bounded(timeout))

    def close(self) -> None:
        self._inner.close()

    def start_tls(self, ssl_context, server_hostname: str | None = None, timeout: float | None = None):
        return _DeadlineStream(self._inner.start_tls(ssl_context, server_hostname, _bounded(timeout)))

    def get_extra_info(self, info: str) -> Any:
        return self._inner.get_extra_info(info)


class _RadarNetworkBackend(httpcore.NetworkBackend):
    """Sync backend with deadlines; when ``guard_ssrf`` it pins connections to validated public IPs.

    The hostname is resolved exactly once here, every address is validated, and the TCP
    connection is made to the validated IP literal (TLS SNI / certificate checks still use the
    hostname: httpcore passes it separately to ``start_tls``). The connected peer address is
    re-checked as a final guard. This closes the check-then-resolve-again (DNS rebinding) gap.
    """

    def __init__(self, *, guard_ssrf: bool) -> None:
        self.guard_ssrf = guard_ssrf
        self._inner = httpcore.SyncBackend()

    def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                    local_address: str | None = None, socket_options: Iterable[Any] | None = None):
        if not self.guard_ssrf:
            stream = self._inner.connect_tcp(host, port, _bounded(timeout), local_address, socket_options)
            return _DeadlineStream(stream)
        last: Exception | None = None
        for ip in resolve_public(host.strip("[]"), port):
            try:
                stream = self._inner.connect_tcp(ip, port, _bounded(timeout), local_address, socket_options)
            except (httpcore.ConnectError, httpcore.ConnectTimeout) as e:
                last = e
                continue
            sock = stream.get_extra_info("socket")
            peer = sock.getpeername()[0] if sock is not None else ip
            if not _is_public_ip(str(peer)):
                stream.close()
                raise UnsafeURLError(f"{host} connected to non-public address {peer}")
            return _DeadlineStream(stream)
        assert last is not None
        raise last

    def connect_unix_socket(self, path: str, timeout: float | None = None,
                            socket_options: Iterable[Any] | None = None):
        raise UnsafeURLError("unix sockets are not allowed")

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _transport(guard_ssrf: bool) -> httpx.HTTPTransport:
    transport = httpx.HTTPTransport()
    transport._pool = httpcore.ConnectionPool(  # swap in our network backend (httpx has no public hook)
        ssl_context=httpx.create_ssl_context(),
        max_connections=100,
        max_keepalive_connections=20,
        keepalive_expiry=5.0,
        network_backend=_RadarNetworkBackend(guard_ssrf=guard_ssrf),
    )
    return transport


def _origin(url: str) -> tuple[str, str, int | None]:
    p = urlparse(url)
    port = p.port or {"http": 80, "https": 443}.get(p.scheme)
    return p.scheme, (p.hostname or "").lower().rstrip("."), port


@dataclass
class HttpStats:
    requests: int = 0
    errors: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    rate_limit_remaining: int | None = None

    @property
    def p50(self) -> float | None:
        return statistics.median(self.latencies_ms) if self.latencies_ms else None


class HttpClient:
    """Thin sync wrapper over httpx with politeness, retries, stats and cassettes.

    mode: "live" (default) | "record" (live + write cassettes) | "replay" (cassettes only)
    """

    def __init__(
        self,
        *,
        min_interval: float = 0.0,
        headers: dict[str, str] | None = None,
        timeout: float = 20.0,
        cassette_dir: Path | None = None,
        mode: str = "live",
        max_retries: int = 3,
        guard_ssrf: bool = False,
        attempt_deadline: float | None = None,
    ) -> None:
        self.min_interval = min_interval
        self.mode = mode
        self.cassette_dir = cassette_dir
        self.max_retries = max_retries
        self.guard_ssrf = guard_ssrf
        # Wall-clock cap per attempt (connect + headers + body). httpx's timeout is per socket
        # operation only, so a server dripping one byte per (timeout - 1)s would never time out.
        self.attempt_deadline = attempt_deadline if attempt_deadline is not None else timeout * 2
        self._job_deadline: float | None = None  # absolute monotonic deadline for a multi-request job
        self.stats = HttpStats()
        self._last_req: dict[str, float] = {}
        self._lock = threading.Lock()
        base_headers = {"User-Agent": get_settings().http_user_agent}
        base_headers.update(headers or {})
        self._client = httpx.Client(
            headers=base_headers,
            timeout=timeout,
            follow_redirects=False,
            transport=_transport(guard_ssrf),
            # Guarded clients connect directly: an env proxy would resolve the target itself and bypass
            # the connect-time address check.
            trust_env=not guard_ssrf,
        )

    # ── deadlines ──
    def set_deadline(self, seconds: float | None) -> None:
        """Overall wall-clock budget for every later request on this client (None = no job deadline)."""
        self._job_deadline = None if seconds is None else time.monotonic() + seconds

    @contextlib.contextmanager
    def deadline(self, seconds: float) -> Iterator[None]:
        """Bound a multi-request job (e.g. a site crawl): requests after ``seconds`` raise DeadlineExceeded."""
        prev = self._job_deadline
        new = time.monotonic() + seconds
        self._job_deadline = new if prev is None else min(prev, new)
        try:
            yield
        finally:
            self._job_deadline = prev

    def _check_job_deadline(self) -> None:
        if self._job_deadline is not None and time.monotonic() >= self._job_deadline:
            raise DeadlineExceeded("job deadline exceeded")

    # ── politeness ──
    def _throttle(self, host: str) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            last = self._last_req.get(host, 0.0)
            wait = self.min_interval - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            self._last_req[host] = time.monotonic()

    # ── cassettes ──
    def _cassette_path(self, method: str, url: str, params: dict | None, headers: dict | None) -> Path | None:
        if not self.cassette_dir:
            return None
        accept = (headers or {}).get("Accept", "")
        basis = json.dumps([method, url, sorted((params or {}).items()), accept], default=str)
        return self.cassette_dir / f"{hashlib.sha256(basis.encode()).hexdigest()[:24]}.json"

    def request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        max_redirects: int = 4,
        max_bytes: int | None = None,
    ) -> httpx.Response:
        cpath = self._cassette_path(method, url, params, headers)
        if self.mode == "replay":
            if not cpath or not cpath.exists():
                raise FileNotFoundError(f"no cassette for {method} {url} {params}")
            data = json.loads(cpath.read_text())
            return httpx.Response(
                status_code=data["status"],
                headers=data["headers"],
                content=data["body"].encode(),
                request=httpx.Request(method, url, params=params),
            )

        current = url
        first_origin = _origin(url)
        strip_credentials = False
        resp: httpx.Response | None = None
        for _hop in range(max_redirects + 1):
            if self.guard_ssrf:
                current = sanitize_url(current)
                assert_public_host(current)  # fail fast; the network backend re-validates at connect time
            if _origin(current) != first_origin:
                # Once a redirect leaves the original origin, credentials stay off for the rest of the chain.
                strip_credentials = True
            host = urlparse(current).netloc
            resp = self._send_with_retries(
                method, current, params=params, headers=headers, host=host, max_bytes=max_bytes or MAX_BODY_BYTES,
                strip_credentials=strip_credentials,
            )
            if resp.is_redirect and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                params = None
                continue
            break
        assert resp is not None

        if self.mode == "record" and cpath is not None:
            cpath.parent.mkdir(parents=True, exist_ok=True)
            keep = {k: v for k, v in resp.headers.items() if k.lower() in ("content-type", "link", "etag")}
            cpath.write_text(json.dumps({"status": resp.status_code, "headers": keep, "body": resp.text}))
        return resp

    def _build(self, method, url, *, params, headers, strip_credentials) -> httpx.Request:
        req = self._client.build_request(method, url, params=params, headers=headers)
        if strip_credentials:
            for name in CREDENTIAL_HEADERS:
                req.headers.pop(name, None)
        return req

    def _sleep_before_retry(self, delay: float) -> None:
        if self._job_deadline is not None and time.monotonic() + delay >= self._job_deadline:
            raise DeadlineExceeded("job deadline exceeded while backing off")
        time.sleep(delay)

    def _send_with_retries(self, method, url, *, params, headers, host, max_bytes,
                           strip_credentials: bool = False) -> httpx.Response:
        attempt = 0
        while True:
            self._check_job_deadline()
            if self.guard_ssrf and attempt:
                assert_public_host(url)  # re-validate on every retry (DNS may have changed)
            self._throttle(host)
            t0 = time.perf_counter()
            deadline = time.monotonic() + self.attempt_deadline
            if self._job_deadline is not None:
                deadline = min(deadline, self._job_deadline)
            token = _attempt_deadline.set(deadline)
            try:
                req = self._build(method, url, params=params, headers=headers, strip_credentials=strip_credentials)
                r = self._client.send(req, stream=True)
                try:
                    body = b""
                    truncated = False
                    for chunk in r.iter_bytes():  # already decompressed
                        body += chunk
                        if len(body) > max_bytes:
                            truncated = True
                            break
                        if time.monotonic() >= deadline:
                            raise DeadlineExceeded(f"response body from {host} exceeded the wall-clock deadline")
                    # The body is decoded; drop encoding/length headers so httpx doesn't decode it twice.
                    hdrs = [(k, v) for k, v in r.headers.multi_items()
                            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                    if truncated:
                        hdrs.append(("x-radar-truncated", "1"))
                    resp = httpx.Response(status_code=r.status_code, headers=hdrs, content=body, request=r.request)
                finally:
                    r.close()
            except DeadlineExceeded:
                self.stats.errors += 1
                raise
            except (httpx.TransportError, httpx.TimeoutException):
                self.stats.errors += 1
                if time.monotonic() >= deadline:
                    # A socket timeout clamped to the wall-clock budget: the budget is spent, don't retry.
                    raise DeadlineExceeded(f"request to {host} exceeded the wall-clock deadline") from None
                attempt += 1
                if attempt > self.max_retries:
                    raise
                self._sleep_before_retry(min(2**attempt, 20))
                continue
            finally:
                _attempt_deadline.reset(token)
                self.stats.requests += 1
                self.stats.latencies_ms.append((time.perf_counter() - t0) * 1000)

            rl = resp.headers.get("x-ratelimit-remaining")
            if rl and rl.isdigit():
                self.stats.rate_limit_remaining = int(rl)
            if resp.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                attempt += 1
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt * 1.5, 30)
                self._sleep_before_retry(min(delay, 60))
                continue
            if resp.status_code >= 400:
                self.stats.errors += 1
            return resp

    def get(self, url: str, **kw) -> httpx.Response:
        return self.request("GET", url, **kw)

    def get_json(self, url: str, **kw) -> Any:
        r = self.get(url, **kw)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self._client.close()


class RobotsCache:
    """robots.txt checker. Unreachable robots.txt → allowed (standard crawler behaviour)."""

    def __init__(self, client: HttpClient, agent: str = "AntiRadar") -> None:
        self.client = client
        self.agent = agent
        self._cache: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        base = f"{p.scheme}://{p.netloc}"
        if base not in self._cache:
            rp = urllib.robotparser.RobotFileParser()
            try:
                r = self.client.get(base + "/robots.txt")
                if r.status_code == 200:
                    rp.parse(r.text.splitlines())
                    self._cache[base] = rp
                elif r.status_code in (401, 403):
                    rp.disallow_all = True
                    self._cache[base] = rp
                else:
                    self._cache[base] = None
            except Exception:  # noqa: BLE001
                self._cache[base] = None
        rp = self._cache[base]
        return True if rp is None else rp.can_fetch(self.agent, url)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "ignore")).hexdigest()

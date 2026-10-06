"""Hardened HTTP layer shared by every provider.

* SSRF guard for user-supplied URLs: http(s) only, no credentials in URL,
  DNS-resolved address must be public; re-checked on every redirect.
* Per-host politeness (minimum interval between requests) + robots.txt for crawling.
* Retries with backoff on 429/5xx; honours Retry-After.
* Per-run stats (requests, latency, rate-limit remaining) for observability.
* Record / replay cassettes so provider parsing is testable offline and the
  demo snapshot can be captured from real APIs.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import statistics
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse, urlunparse

import httpx

from radar.settings import get_settings

MAX_BODY_BYTES = 3 * 1024 * 1024
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal", "metadata"}


class UnsafeURLError(ValueError):
    pass


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


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped and not _is_public_ip(str(addr.ipv4_mapped)))
    )


def assert_public_host(url: str) -> None:
    host = urlparse(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UnsafeURLError(f"cannot resolve {host}") from e
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise UnsafeURLError(f"{host} resolves to non-public address {ip}")


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
    ) -> None:
        self.min_interval = min_interval
        self.mode = mode
        self.cassette_dir = cassette_dir
        self.max_retries = max_retries
        self.guard_ssrf = guard_ssrf
        self.stats = HttpStats()
        self._last_req: dict[str, float] = {}
        self._lock = threading.Lock()
        base_headers = {"User-Agent": get_settings().http_user_agent}
        base_headers.update(headers or {})
        self._client = httpx.Client(
            headers=base_headers,
            timeout=timeout,
            follow_redirects=False,
        )

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
        resp: httpx.Response | None = None
        for _hop in range(max_redirects + 1):
            if self.guard_ssrf:
                current = sanitize_url(current)
                assert_public_host(current)
            host = urlparse(current).netloc
            resp = self._send_with_retries(
                method, current, params=params, headers=headers, host=host, max_bytes=max_bytes or MAX_BODY_BYTES
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

    def _send_with_retries(self, method, url, *, params, headers, host, max_bytes) -> httpx.Response:
        attempt = 0
        while True:
            self._throttle(host)
            t0 = time.perf_counter()
            try:
                with self._client.stream(method, url, params=params, headers=headers) as r:
                    body = b""
                    truncated = False
                    for chunk in r.iter_bytes():  # already decompressed
                        body += chunk
                        if len(body) > max_bytes:
                            truncated = True
                            break
                    # The body is decoded; drop encoding/length headers so httpx doesn't decode it twice.
                    hdrs = [(k, v) for k, v in r.headers.multi_items()
                            if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")]
                    if truncated:
                        hdrs.append(("x-radar-truncated", "1"))
                    resp = httpx.Response(status_code=r.status_code, headers=hdrs, content=body, request=r.request)
            except (httpx.TransportError, httpx.TimeoutException):
                self.stats.errors += 1
                attempt += 1
                if attempt > self.max_retries:
                    raise
                time.sleep(min(2**attempt, 20))
                continue
            finally:
                self.stats.requests += 1
                self.stats.latencies_ms.append((time.perf_counter() - t0) * 1000)

            rl = resp.headers.get("x-ratelimit-remaining")
            if rl and rl.isdigit():
                self.stats.rate_limit_remaining = int(rl)
            if resp.status_code in (429, 502, 503, 504) and attempt < self.max_retries:
                attempt += 1
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(2**attempt * 1.5, 30)
                time.sleep(min(delay, 60))
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

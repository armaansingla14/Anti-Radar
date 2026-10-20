"""In-process token-bucket rate limiting for expensive endpoints (investigate, memo, ingest).

Single-user internal tool → per-client-IP buckets in memory are sufficient.
:class:`TokenBucket` is the same limiter for code paths without a request (e.g. the
LLM branch of search parsing), keyed process-wide.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

_buckets: dict[tuple[str, str], tuple[float, float]] = {}


def _now() -> float:  # indirection so tests can freeze the limiter's clock without touching time.monotonic
    return time.monotonic()

_lock = threading.Lock()


CLIENT_IP_HEADER = "x-radar-client-ip"


def client_id(request: Request) -> str:
    """Per-visitor key. Behind the Next.js proxy every request comes from the proxy's address, so the proxy
    forwards the visitor IP in X-Radar-Client-IP; it is trusted only when TRUST_PROXY_CLIENT_IP is set."""
    from radar.settings import get_settings

    if get_settings().trust_proxy_client_ip:
        forwarded = (request.headers.get(CLIENT_IP_HEADER) or "").split(",")[0].strip()
        if forwarded:
            return forwarded[:64]
    return request.client.host if request.client else "local"


def rate_limit(name: str, per_minute: int):
    capacity = float(per_minute)
    refill = per_minute / 60.0

    def dependency(request: Request) -> None:
        client = client_id(request)
        key = (name, client)
        now = _now()
        with _lock:
            tokens, last = _buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill)
            if tokens < 1.0:
                retry = int((1.0 - tokens) / refill) + 1
                raise HTTPException(429, detail=f"Rate limit for {name}: {per_minute}/min", headers={"Retry-After": str(retry)})
            _buckets[key] = (tokens - 1.0, now)

    return dependency


class TokenBucket:
    """Process-wide token bucket: ``try_acquire()`` is True if a call is allowed now."""

    def __init__(self, name: str, per_minute: int | None = None, *, per_minute_fn=None) -> None:
        self.name = name
        self._per_minute = per_minute
        self._per_minute_fn = per_minute_fn  # read the limit lazily (settings may change in tests)

    @property
    def per_minute(self) -> int:
        return int(self._per_minute_fn() if self._per_minute_fn else (self._per_minute or 0))

    def try_acquire(self) -> bool:
        per_minute = self.per_minute
        if per_minute <= 0:
            return False
        capacity, refill = float(per_minute), per_minute / 60.0
        key = (self.name, "*")
        now = _now()
        with _lock:
            tokens, last = _buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill)
            if tokens < 1.0:
                _buckets[key] = (tokens, now)
                return False
            _buckets[key] = (tokens - 1.0, now)
            return True


def reset() -> None:
    with _lock:
        _buckets.clear()

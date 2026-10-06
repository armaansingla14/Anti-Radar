"""In-process token-bucket rate limiting for expensive endpoints (investigate, memo, ingest).

Single-user internal tool → per-client-IP buckets in memory are sufficient.
"""

from __future__ import annotations

import threading
import time

from fastapi import HTTPException, Request

_buckets: dict[tuple[str, str], tuple[float, float]] = {}
_lock = threading.Lock()


def rate_limit(name: str, per_minute: int):
    capacity = float(per_minute)
    refill = per_minute / 60.0

    def dependency(request: Request) -> None:
        client = request.client.host if request.client else "local"
        key = (name, client)
        now = time.monotonic()
        with _lock:
            tokens, last = _buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - last) * refill)
            if tokens < 1.0:
                retry = int((1.0 - tokens) / refill) + 1
                raise HTTPException(429, detail=f"Rate limit for {name}: {per_minute}/min", headers={"Retry-After": str(retry)})
            _buckets[key] = (tokens - 1.0, now)

    return dependency


def reset() -> None:
    with _lock:
        _buckets.clear()

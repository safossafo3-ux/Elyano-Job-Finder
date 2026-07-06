"""
Per-user rate limiting middleware — Phase 3.

Uses an in-memory sliding-window counter. Each authenticated user gets
a fixed budget of API calls per minute. Unauthenticated callers share a
single IP-based budget (to prevent anonymous abuse).

Configuration:
  RATE_LIMIT_PER_MINUTE_AUTH   default 60
  RATE_LIMIT_PER_MINUTE_ANON   default 15

Exempt paths (no limit applied):
  /health, /static/*, /, /api/regions, /api/categories, /api/stats
"""

import time
import logging
import os
from collections import defaultdict
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

_AUTH_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE_AUTH", "60"))
_ANON_LIMIT = int(os.getenv("RATE_LIMIT_PER_MINUTE_ANON", "15"))

# Exempt paths — read-only public endpoints and the page itself
_EXEMPT_PREFIXES = (
    "/health",
    "/static/",
)
_EXEMPT_EXACT = {"/", "/api/regions", "/api/categories", "/api/stats",
                 "/favicon.ico"}


class _Window:
    """Sliding 60-second window of timestamps."""
    __slots__ = ("hits",)

    def __init__(self):
        self.hits: list[float] = []

    def consume(self, now: float, limit: int) -> bool:
        # Drop hits older than 60s
        cutoff = now - 60
        self.hits = [t for t in self.hits if t >= cutoff]
        if len(self.hits) >= limit:
            return False
        self.hits.append(now)
        return True


# Per-key buckets — keyed by either "user:<id>" or "ip:<addr>"
_buckets: dict[str, _Window] = defaultdict(_Window)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "0.0.0.0"


def _is_exempt(path: str) -> bool:
    if path in _EXEMPT_EXACT:
        return True
    for p in _EXEMPT_PREFIXES:
        if path.startswith(p):
            return True
    return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_exempt(path):
            return await call_next(request)

        # Try to read user id from session cookie — cheap, no DB call here
        # (We just key by cookie hash to avoid DB lookups on every request.)
        cookie = request.cookies.get("session") or ""
        if cookie:
            key = f"user:{cookie[:24]}"  # first 24 chars is enough entropy
            limit = _AUTH_LIMIT
        else:
            key = f"ip:{_client_ip(request)}"
            limit = _ANON_LIMIT

        now = time.time()
        bucket = _buckets[key]
        if not bucket.consume(now, limit):
            logger.warning(f"Rate limit exceeded for {key} on {path}")
            return JSONResponse(
                status_code=429,
                content={
                    "detail": "Too many requests. Please slow down.",
                    "retry_after_seconds": 60,
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

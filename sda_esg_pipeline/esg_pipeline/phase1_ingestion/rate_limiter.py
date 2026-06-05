"""API protection: rate limiting & daily quota (SDAD §1 step 3, §3.2).

Before any external API call the worker consults Redis to enforce:
  * a **rate limit** — at most N calls per rolling window, and
  * a **daily quota** — e.g. Google CSE's 100 requests/day.

Both use atomic ``INCR`` + ``EXPIRE`` so concurrent workers stay consistent.
The client is injected, which keeps the logic unit-testable without a live
Redis (see tests). Use :func:`build_redis_client` for the real thing.
"""
from __future__ import annotations

import logging

from ..common.exceptions import QuotaExceededException

logger = logging.getLogger(__name__)


def build_redis_client():
    """Construct a real Redis client from :data:`Settings` (lazy import)."""
    import redis

    from ..common.config import settings

    return redis.Redis(
        host=settings.redis_host, port=settings.redis_port, db=settings.redis_db
    )


class RateLimiter:
    """Redis-backed fixed-window rate limiter + daily quota guard."""

    def __init__(self, redis_client):
        self._redis = redis_client

    def check_rate_limit(self, key: str, limit: int, window_seconds: int) -> bool:
        """Return True if a call is allowed; increments the window counter.

        First call in a window sets the TTL; subsequent calls only increment.
        Returns False once ``limit`` is exceeded within the window.
        """
        redis_key = f"ratelimit:{key}"
        count = self._redis.incr(redis_key)
        if count == 1:
            self._redis.expire(redis_key, window_seconds)
        allowed = count <= limit
        if not allowed:
            logger.warning("Rate limit hit for %s (%d/%d)", key, count, limit)
        return allowed

    def consume_quota(self, key: str, daily_quota: int) -> int:
        """Consume one unit of a per-day quota; raise when exhausted.

        Returns the remaining quota. Raises :class:`QuotaExceededException`
        (circuit-breaker trip) once the daily allowance is used up.
        """
        redis_key = f"quota:{key}"
        used = self._redis.incr(redis_key)
        if used == 1:
            self._redis.expire(redis_key, 86_400)  # reset after 24h
        remaining = daily_quota - used
        if remaining < 0:
            logger.error("Daily quota exhausted for %s (%d/%d)", key, used, daily_quota)
            raise QuotaExceededException(
                f"Daily quota of {daily_quota} exhausted for '{key}'."
            )
        return remaining

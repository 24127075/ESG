"""Crawler Tier 2 — Search Engine Automation API (SDAD §3.2).

Works around the Google Custom Search Engine free-tier quota (100 req/day) by:
  * caching resolved report URLs in Redis for 30 days,
  * blocking known document-piracy / fake-IR domains,
  * tripping a circuit breaker on HTTP 429 and pushing the task to a DLQ.
"""
from __future__ import annotations

import logging
import os

from ..common.exceptions import QuotaExceededException

logger = logging.getLogger(__name__)

DEVELOPER_KEY = os.getenv("GOOGLE_API_KEY")
CX = os.getenv("GOOGLE_CX_ID")

TRUSTED_TLDS = (".vn", ".com.vn")
SCAM_KEYWORDS = ("tailieu", "doc", "scribd", "123doc")

CACHE_TTL_SECONDS = 2_592_000  # 30 days


def _get_redis():
    """Lazily build a Redis client from env (import-safe when redis absent)."""
    import redis

    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
    )


def is_trusted_domain(url: str, official_domain: str) -> bool:
    """Reject piracy/fake-IR domains; accept the issuer's own domain or VN TLDs."""
    try:
        domain = url.split("/")[2].lower()
    except IndexError:
        return False
    if any(k in domain for k in SCAM_KEYWORDS):
        return False
    return official_domain in domain or domain.endswith(TRUSTED_TLDS)


def search_esg_report(
    ticker: str,
    year: int,
    official_domain: str,
    redis_client=None,
    dlq_push=None,
) -> str | None:
    """Resolve the canonical ESG/annual-report PDF URL for a ticker-year.

    Returns the URL, or None if nothing trusted was found. On quota exhaustion
    (HTTP 429) the circuit breaker trips: ``dlq_push`` (if provided) is invoked
    with the task payload and :class:`QuotaExceededException` is raised.
    """
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError

    redis_client = redis_client or _get_redis()
    cache_key = f"search_{ticker}_{year}"

    cached = redis_client.get(cache_key)
    if cached:
        return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    try:
        service = build("customsearch", "v1", developerKey=DEVELOPER_KEY)
        query = f'"{ticker}" ("bao cao thuong nien" OR "phat trien ben vung") {year}'
        res = (
            service.cse()
            .list(q=query, cx=CX, fileType="pdf", num=5)
            .execute()
        )
        for item in res.get("items", []):
            if is_trusted_domain(item["link"], official_domain):
                redis_client.setex(cache_key, CACHE_TTL_SECONDS, item["link"])
                return item["link"]
        return None
    except HttpError as exc:
        if exc.resp.status == 429:
            # Circuit breaker: stop hammering the API, route task to the DLQ.
            if dlq_push is not None:
                dlq_push({"ticker": ticker, "year": year, "reason": "quota_429"})
            logger.error("Google CSE quota reached — halting searches.")
            raise QuotaExceededException(
                "Google API quota reached. Halting searches."
            ) from exc
        raise

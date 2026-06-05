"""Crawler Tier 2 — free Python search for ESG report discovery (SDAD §3.2).

Replaces the SDAD's **Google Custom Search Engine** (which needs a paid API key
and is capped at 100 requests/day) with the **free, keyless DuckDuckGo** search
via the ``ddgs`` package. The rest of the §3.2 contract is preserved:

  * filter out document-piracy / fake-IR domains (``is_trusted_domain``),
  * optionally cache the resolved report URL in Redis for 30 days,
  * trip a circuit breaker on rate-limiting and push the task to a DLQ.

No environment variable / API key is required.
"""
from __future__ import annotations

import logging
import os
from urllib.parse import urlparse

from ..common.exceptions import QuotaExceededException

logger = logging.getLogger(__name__)

TRUSTED_TLDS = (".vn", ".com.vn")

# Known document-piracy / fake-IR hosts to reject. Matched by *host* (exact or
# subdomain), NOT by substring — the old SDAD code used ``'doc' in domain`` which
# false-blocks any legitimate host containing the substring "doc", while
# ``'123doc'`` already contains ``'doc'`` and was redundant. See
# docs/DANH_GIA_SDA.md issue #2.
SCAM_DOMAINS = frozenset(
    {
        "tailieu.vn",
        "123doc.vn",
        "123doc.net",
        "123docz.net",
        "scribd.com",
        "slideshare.net",
        "academia.edu",
        "coursehero.com",
        "studocu.com",
        "text.123docz.net",
    }
)

CACHE_TTL_SECONDS = 2_592_000  # 30 days
DEFAULT_MAX_RESULTS = 10


def _get_redis():
    """Lazily build a Redis client from env (import-safe when redis absent)."""
    import redis

    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
    )


def _hostname(url: str) -> str | None:
    """Robustly extract a lowercase hostname from a URL.

    Tolerates URLs without a scheme (``example.com/x.pdf``), with ports, or with
    userinfo — unlike the SDAD's ``url.split('/')[2]`` which breaks on all three.
    """
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="https")
    host = parsed.hostname
    return host.lower() if host else None


def _is_piracy_host(host: str) -> bool:
    """True if ``host`` is, or is a subdomain of, a known piracy/fake-IR host."""
    return any(host == bad or host.endswith("." + bad) for bad in SCAM_DOMAINS)


def is_trusted_domain(url: str, official_domain: str) -> bool:
    """Reject piracy/fake-IR domains; accept the issuer's own domain or VN TLDs.

    A piracy host is rejected even when it sits under a trusted TLD (e.g.
    ``123doc.vn``), so the blocklist is checked *before* the TLD allowance.
    """
    host = _hostname(url)
    if not host:
        return False
    if _is_piracy_host(host):
        return False
    official = official_domain.lower()
    if host == official or host.endswith("." + official):
        return True
    return host.endswith(TRUSTED_TLDS)


def _looks_like_pdf_url(url: str) -> bool:
    """Heuristic: the URL path points at a .pdf (allowing a query string)."""
    path = urlparse(url).path.lower()
    return path.endswith(".pdf") or ".pdf" in url.lower()


def _pick_report_url(results: list[dict], official_domain: str) -> str | None:
    """Choose the best report URL from search hits (pure, unit-testable).

    Order of preference: a trusted-domain PDF → any non-piracy PDF → ``None``.
    Piracy hosts are always discarded.
    """
    non_piracy = []
    for item in results:
        url = (item or {}).get("href") or ""
        host = _hostname(url)
        if not url or not host or _is_piracy_host(host):
            continue
        non_piracy.append(url)

    pdfs = [u for u in non_piracy if _looks_like_pdf_url(u)]
    trusted = [u for u in pdfs if is_trusted_domain(u, official_domain)]
    if trusted:
        return trusted[0]
    if pdfs:
        logger.warning(
            "No trusted-domain PDF for %s; using best-effort non-piracy PDF.",
            official_domain,
        )
        return pdfs[0]
    return None


def _ddg_search(query: str, max_results: int) -> list[dict]:
    """Run a free DuckDuckGo text search; raise QuotaExceededException on limit."""
    from ddgs import DDGS

    try:
        with DDGS() as ddgs:
            return list(ddgs.text(query, max_results=max_results))
    except Exception as exc:  # noqa: BLE001 - ddgs raises several concrete types
        if "ratelimit" in type(exc).__name__.lower():
            raise QuotaExceededException(
                "DuckDuckGo rate limit reached. Halting searches."
            ) from exc
        logger.warning("DuckDuckGo search failed for %r: %s", query, exc)
        return []


def search_esg_report(
    ticker: str,
    year: int,
    official_domain: str,
    redis_client=None,
    dlq_push=None,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> str | None:
    """Resolve the canonical ESG/annual-report PDF URL for a ticker-year.

    Uses the free DuckDuckGo search (no API key). Returns the URL, or None if
    nothing suitable was found. Redis caching is **optional** — pass
    ``redis_client`` to enable a 30-day cache; omit it to run without any infra.
    On rate-limiting the circuit breaker trips: ``dlq_push`` (if provided) is
    invoked with the task payload and :class:`QuotaExceededException` is raised.
    """
    cache_key = f"search_{ticker}_{year}"
    if redis_client is not None:
        cached = redis_client.get(cache_key)
        if cached:
            return cached.decode("utf-8") if isinstance(cached, bytes) else cached

    query = (
        f'{ticker} "bao cao thuong nien" OR "bao cao phat trien ben vung" '
        f"{year} filetype:pdf"
    )
    try:
        results = _ddg_search(query, max_results)
    except QuotaExceededException:
        if dlq_push is not None:
            dlq_push({"ticker": ticker, "year": year, "reason": "ddg_ratelimit"})
        logger.error("DuckDuckGo quota/rate limit — halting searches.")
        raise

    url = _pick_report_url(results, official_domain)
    if url and redis_client is not None:
        redis_client.setex(cache_key, CACHE_TTL_SECONDS, url)
    return url

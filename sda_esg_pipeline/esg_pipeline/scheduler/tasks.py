"""Celery tasks — the executable Phase 1/Phase 2 workflow (SDAD §1).

Sequence (per the architecture diagram):
    Beat → scan_quantitative / crawl_reports / poll_rss
         → (rate-limit guard) → fetch / search / download
         → malware scan + SHA-256 dedup → Local + S3 storage
         → metadata/state in PostgreSQL → enqueue process_document (Phase 2)

Quota exhaustion (HTTP 429) trips a circuit breaker: the offending payload is
pushed to the Dead Letter Queue task and searches halt for the window.
"""
from __future__ import annotations

import logging

from celery import shared_task

from ..common.config import settings
from ..common.exceptions import PipelineError, QuotaExceededException
from ..common.monitoring import compute_error_rate, send_slack_alert
from ..common.state import DocumentState
from .celery_app import app

logger = logging.getLogger(__name__)


# ── Dead Letter Queue ───────────────────────────────────────────────────────
@app.task(name="esg_pipeline.scheduler.tasks.dead_letter", queue="dlq")
def dead_letter(payload: dict) -> None:
    """Terminal sink for tasks that tripped the circuit breaker."""
    logger.error("DLQ received payload: %s", payload)


def _dlq_push(payload: dict) -> None:
    dead_letter.apply_async(args=[payload], queue="dlq")


# ── Phase 1: Quantitative engine ─────────────────────────────────────────────
@shared_task(
    bind=True,
    name="esg_pipeline.scheduler.tasks.scan_quantitative",
    autoretry_for=(PipelineError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def scan_quantitative(self, tickers: list[str] | None = None, year: int = 2025) -> dict:
    """Fetch FF6 quantitative inputs for the ticker universe."""
    from ..phase1_ingestion.quantitative import fetch_financial_data

    tickers = tickers or _load_universe()
    failed = 0
    for ticker in tickers:
        try:
            fetch_financial_data(ticker, year)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            logger.warning("Quant fetch failed for %s %s: %s", ticker, year, exc)

    _alert_if_degraded(failed, len(tickers))
    return {"total": len(tickers), "failed": failed}


# ── Phase 1: Unstructured crawler ────────────────────────────────────────────
@shared_task(name="esg_pipeline.scheduler.tasks.crawl_reports")
def crawl_reports(year: int = 2025, tickers: list[str] | None = None) -> dict:
    """Discover, download, dedup and store ESG/annual reports, then queue Phase 2."""
    from ..phase1_ingestion.crawler_tier2 import search_esg_report
    from ..phase1_ingestion.downloader import download_document
    from ..phase1_ingestion.metadata_repo import get_repository
    from ..phase1_ingestion.rate_limiter import RateLimiter, build_redis_client
    from ..phase1_ingestion.storage import StorageManager

    universe = tickers or _load_universe()
    redis_client = build_redis_client()
    limiter = RateLimiter(redis_client)
    storage = StorageManager()
    repo = get_repository()

    saved, skipped, failed = 0, 0, 0
    try:
        for ticker in universe:
            official_domain = _official_domain(ticker)
            try:
                # Politeness rate-limit against the free DuckDuckGo search
                # (no daily API quota anymore — Tier 2 is keyless, §3.2).
                limiter.check_rate_limit("ddg_search", limit=60, window_seconds=60)
                url = search_esg_report(
                    ticker, year, official_domain,
                    redis_client=redis_client, dlq_push=_dlq_push,
                )
                if not url:
                    skipped += 1
                    continue

                content, digest = download_document(url)
                if repo.find_by_hash(digest):
                    skipped += 1  # already ingested (dedup)
                    continue

                doc_id = repo.upsert_document(
                    ticker, year, pdf_type_flag="TEXT_BASED", content_hash=digest
                )
                key = f"reports/{ticker}/{year}.pdf"
                path = storage.save_local(content, f"reports/{ticker}", f"{year}.pdf")
                if storage.s3_bucket:
                    storage.upload_s3(content, key)
                repo.upsert_document(ticker, year, storage_path=path)
                repo.transition(doc_id, DocumentState.DOWNLOADED)
                saved += 1
            except QuotaExceededException:
                logger.error("Quota circuit breaker tripped — halting crawl.")
                break
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.warning("Crawl failed for %s: %s", ticker, exc)
    finally:
        repo.close()

    _alert_if_degraded(failed, max(len(universe), 1))
    return {"saved": saved, "skipped": skipped, "failed": failed}


# ── Phase 1: RSS news ────────────────────────────────────────────────────────
@shared_task(name="esg_pipeline.scheduler.tasks.poll_rss")
def poll_rss() -> dict:
    """Pull RSS feeds and persist only previously-unseen items (§3.3, §3.4)."""
    from ..phase1_ingestion.crawler_tier3 import aggregate_all
    from ..phase1_ingestion.deduplication import check_and_save_news, init_db

    init_db(settings.checkpoint_db)
    new_items = 0
    for item in aggregate_all():
        if check_and_save_news(item.as_dict(), settings.checkpoint_db):
            new_items += 1
    return {"new_items": new_items}


# ── Phase 2: Preprocessing ───────────────────────────────────────────────────
@shared_task(
    bind=True,
    name="esg_pipeline.scheduler.tasks.process_document",
    autoretry_for=(PipelineError,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
)
def process_document(self, metadata_path: str, output_dir: str = "./out") -> int:
    """Run Phase 2 for one document and advance its state to COMPLETED."""
    from ..phase2_preprocessing.orchestrator import process_single_document

    count = process_single_document(metadata_path, output_dir, settings.taxonomy_path)
    logger.info("process_document(%s) → %d chunks", metadata_path, count)
    return count


# ── Helpers ──────────────────────────────────────────────────────────────────
def _alert_if_degraded(failed: int, total: int) -> None:
    rate = compute_error_rate(failed, total)
    send_slack_alert(rate, failed, total)


def _load_universe() -> list[str]:
    """The ~310-ticker universe. Stubbed; wire to PostgreSQL/config in prod."""
    return ["VNM", "FPT", "HPG", "VCB", "MWG"]


def _official_domain(ticker: str) -> str:
    """Issuer's official IR domain. Stubbed; resolve from a registry in prod."""
    return {
        "VNM": "vinamilk.com.vn",
        "FPT": "fpt.com.vn",
        "HPG": "hoaphat.com.vn",
        "VCB": "vietcombank.com.vn",
        "MWG": "mwg.vn",
    }.get(ticker, f"{ticker.lower()}.com.vn")

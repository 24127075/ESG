"""Celery application + Beat schedule (SDAD §1).

Run the worker and the scheduler as two processes:

    celery -A esg_pipeline.scheduler.celery_app:app worker --loglevel=INFO
    celery -A esg_pipeline.scheduler.celery_app:app beat   --loglevel=INFO

The broker/result backend default to Redis (configurable via env). A
``QuotaExceededException`` raised by a task trips the circuit breaker; the task
is routed to the Dead Letter Queue (``dlq``) instead of being retried forever.
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from ..common.config import settings
from ..common.logging_config import configure_logging

configure_logging()

app = Celery(
    "esg_pipeline",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["esg_pipeline.scheduler.tasks"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Ho_Chi_Minh",
    enable_utc=True,
    task_acks_late=True,                 # redeliver if a worker dies mid-task
    worker_prefetch_multiplier=1,        # fair dispatch for long PDF jobs
    task_reject_on_worker_lost=True,
    task_routes={
        "esg_pipeline.scheduler.tasks.process_document": {"queue": "phase2"},
        "esg_pipeline.scheduler.tasks.*": {"queue": "phase1"},
    },
    task_default_queue="phase1",
)

# Beat schedule — periodic ingestion triggers.
app.conf.beat_schedule = {
    "quant-daily-scan": {
        "task": "esg_pipeline.scheduler.tasks.scan_quantitative",
        # 02:00 every day, after market data settles.
        "schedule": crontab(hour=2, minute=0),
    },
    "crawl-reports-daily": {
        "task": "esg_pipeline.scheduler.tasks.crawl_reports",
        "schedule": crontab(hour=3, minute=0),
    },
    "rss-poll": {
        "task": "esg_pipeline.scheduler.tasks.poll_rss",
        # Third-party news is time-sensitive — poll every 30 minutes.
        "schedule": crontab(minute="*/30"),
    },
}

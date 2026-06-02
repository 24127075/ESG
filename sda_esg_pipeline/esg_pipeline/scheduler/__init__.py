"""Scheduling layer (SDAD §1).

Celery Beat (cron) periodically enqueues ingestion tasks onto a Redis broker;
Celery workers execute them. See :mod:`esg_pipeline.scheduler.celery_app` for
the app + beat schedule and :mod:`esg_pipeline.scheduler.tasks` for the tasks.
"""

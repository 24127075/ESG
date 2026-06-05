"""Monitoring & alerting (SDAD §5).

Error rates are surfaced to Slack via an incoming webhook configured through
the ``SLACK_ALERT_WEBHOOK`` environment variable. A CRITICAL alert fires once
the failure rate reaches the configured threshold (default 15%).
"""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

SLACK_WEBHOOK_URL = os.getenv("SLACK_ALERT_WEBHOOK")
CRITICAL_ERROR_RATE = 15.0  # percent


def compute_error_rate(failed_count: int, total: int) -> float:
    """Failure percentage; 0.0 when nothing has been processed yet."""
    if total <= 0:
        return 0.0
    return (failed_count / total) * 100.0


def send_slack_alert(error_rate: float, failed_count: int, total: int) -> bool:
    """Post a CRITICAL alert to Slack when ``error_rate`` crosses threshold.

    Returns True if an alert was actually dispatched, False otherwise (below
    threshold or no webhook configured). Network errors are swallowed and
    logged so alerting never takes the pipeline down with it.
    """
    if error_rate < CRITICAL_ERROR_RATE or not SLACK_WEBHOOK_URL:
        return False

    msg = {
        "text": (
            f":rotating_light: *CRITICAL ALERT* Tỷ lệ lỗi: {error_rate:.1f}% "
            f"({failed_count}/{total})"
        )
    }
    try:
        requests.post(SLACK_WEBHOOK_URL, json=msg, timeout=5)
        return True
    except requests.RequestException as exc:  # pragma: no cover - network
        logger.error("Failed to deliver Slack alert: %s", exc)
        return False

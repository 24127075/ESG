"""Structured logging setup.

Call :func:`configure_logging` once at process start (worker, beat, CLI). Uses
a concise, timestamped format suitable for shipping to a log aggregator.
"""
from __future__ import annotations

import logging
import os

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def configure_logging(level: str | None = None) -> None:
    """Configure root logging idempotently from ``LOG_LEVEL`` (default INFO)."""
    resolved = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=resolved, format=_FORMAT)
    # Quiet noisy third-party libraries.
    for noisy in ("urllib3", "botocore", "boto3", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

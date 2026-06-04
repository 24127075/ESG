"""Structured logging setup.

Call :func:`configure_logging` once at process start (worker, beat, CLI). Uses
a concise, timestamped format suitable for shipping to a log aggregator.
"""
from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"


def ensure_utf8_io() -> None:
    """Force stdout/stderr to UTF-8 so non-ASCII output never crashes.

    On Windows the console defaults to a legacy code page (cp1252) and any
    library that prints Vietnamese/emoji to stdout — e.g. the ``vnstock``
    banner — raises ``UnicodeEncodeError``. Reconfiguring to UTF-8 with
    ``errors='replace'`` makes the CLI/scripts robust without setting
    ``PYTHONUTF8`` by hand.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):  # detached/closed stream
                pass


def configure_logging(level: str | None = None) -> None:
    """Configure root logging idempotently from ``LOG_LEVEL`` (default INFO)."""
    ensure_utf8_io()
    resolved = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(level=resolved, format=_FORMAT)
    # Quiet noisy third-party libraries.
    for noisy in ("urllib3", "botocore", "boto3", "googleapiclient"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

"""Hierarchical exception types (SDAD §5).

All pipeline failures derive from :class:`PipelineError` so the orchestrator
can distinguish *our* domain errors from unexpected runtime errors, and so the
monitoring layer can compute a meaningful error rate.
"""
from __future__ import annotations


class PipelineError(Exception):
    """Base class for every domain-level error raised by the pipeline."""


class PDFCorruptedException(PipelineError):
    """A PDF could not be opened/parsed, or failed the malware/integrity check."""


class QuotaExceededException(PipelineError):
    """An external API (e.g. Google CSE) returned HTTP 429 / quota exhaustion.

    The crawler treats this as a circuit-breaker trip: the offending task is
    pushed to the Dead Letter Queue (DLQ) and searches halt for the window.
    """


class FallbackUnavailableException(PipelineError):
    """A remote source failed *and* no local static fallback was found."""

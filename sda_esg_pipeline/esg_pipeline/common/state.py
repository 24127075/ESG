"""Document state machine (SDAD §4.2).

Each document's progress is tracked (in PostgreSQL in Production) so the
pipeline can resume from the last good state after a crash instead of
re-processing from scratch.
"""
from __future__ import annotations

import enum


class DocumentState(enum.Enum):
    PENDING = "PENDING"
    DOWNLOADED = "DOWNLOADED"
    EXTRACTED = "EXTRACTED"
    OCR_COMPLETED = "OCR_COMPLETED"
    CLEANED = "CLEANED"
    CHUNKED = "CHUNKED"
    TAGGED = "TAGGED"
    COMPLETED = "COMPLETED"
    ERROR = "ERROR"


# Canonical forward order of the happy-path lifecycle. ERROR is terminal and
# reachable from any state, so it is excluded from the linear ordering.
_ORDER: tuple[DocumentState, ...] = (
    DocumentState.PENDING,
    DocumentState.DOWNLOADED,
    DocumentState.EXTRACTED,
    DocumentState.OCR_COMPLETED,
    DocumentState.CLEANED,
    DocumentState.CHUNKED,
    DocumentState.TAGGED,
    DocumentState.COMPLETED,
)


def can_transition(src: DocumentState, dst: DocumentState) -> bool:
    """Return True if moving from ``src`` to ``dst`` is legal.

    Rules: any state may move to ERROR; otherwise transitions must move
    forward along ``_ORDER`` (skipping is allowed, e.g. a text-based PDF goes
    EXTRACTED -> CLEANED without OCR_COMPLETED).
    """
    if dst is DocumentState.ERROR:
        return True
    if src is DocumentState.ERROR:
        return False
    return _ORDER.index(dst) > _ORDER.index(src)

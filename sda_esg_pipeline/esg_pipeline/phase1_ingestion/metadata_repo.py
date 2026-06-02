"""Document metadata & state-machine persistence (SDAD §1 step 2, §4.2).

Tracks each document's lifecycle so the pipeline can resume after a crash
instead of reprocessing. PostgreSQL is the Production backend; a SQLite backend
is provided for local development and tests. Both share identical SQL because
the schema uses a natural ``doc_id`` key (``{ticker}_{fiscal_year}``) and
``ON CONFLICT`` upserts — features common to both engines.

State transitions are validated against :func:`common.state.can_transition`.
"""
from __future__ import annotations

import logging

from ..common.config import settings
from ..common.state import DocumentState, can_transition

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id        TEXT PRIMARY KEY,
    ticker        TEXT NOT NULL,
    fiscal_year   INTEGER NOT NULL,
    storage_path  TEXT,
    pdf_type_flag TEXT,
    content_hash  TEXT,
    state         TEXT NOT NULL DEFAULT 'PENDING',
    error_message TEXT,
    updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""
_IDX = "CREATE INDEX IF NOT EXISTS idx_documents_hash ON documents (content_hash)"


def make_doc_id(ticker: str, fiscal_year: int) -> str:
    return f"{ticker}_{fiscal_year}"


class MetadataRepository:
    """DB-API-2.0 backed repository (works with psycopg2 or sqlite3)."""

    def __init__(self, conn, placeholder: str = "?"):
        self._conn = conn
        self._ph = placeholder
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        cur = self._conn.cursor()
        cur.execute(_DDL)
        cur.execute(_IDX)
        self._conn.commit()

    def upsert_document(
        self,
        ticker: str,
        fiscal_year: int,
        storage_path: str | None = None,
        pdf_type_flag: str | None = None,
        content_hash: str | None = None,
    ) -> str:
        """Insert or update a document row; returns its ``doc_id``."""
        doc_id = make_doc_id(ticker, fiscal_year)
        p = self._ph
        sql = (
            f"INSERT INTO documents "
            f"(doc_id, ticker, fiscal_year, storage_path, pdf_type_flag, content_hash, state) "
            f"VALUES ({p}, {p}, {p}, {p}, {p}, {p}, {p}) "
            f"ON CONFLICT(doc_id) DO UPDATE SET "
            f"storage_path=COALESCE(excluded.storage_path, documents.storage_path), "
            f"pdf_type_flag=COALESCE(excluded.pdf_type_flag, documents.pdf_type_flag), "
            f"content_hash=COALESCE(excluded.content_hash, documents.content_hash), "
            f"updated_at=CURRENT_TIMESTAMP"
        )
        cur = self._conn.cursor()
        cur.execute(
            sql,
            (
                doc_id,
                ticker,
                fiscal_year,
                storage_path,
                pdf_type_flag,
                content_hash,
                DocumentState.PENDING.value,
            ),
        )
        self._conn.commit()
        return doc_id

    def get_state(self, doc_id: str) -> DocumentState | None:
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT state FROM documents WHERE doc_id = {self._ph}", (doc_id,)
        )
        row = cur.fetchone()
        return DocumentState(row[0]) if row else None

    def transition(
        self, doc_id: str, new_state: DocumentState, error_message: str | None = None
    ) -> None:
        """Move a document to ``new_state`` after validating the transition."""
        current = self.get_state(doc_id)
        if current is None:
            raise KeyError(f"Unknown document: {doc_id}")
        if not can_transition(current, new_state):
            raise ValueError(
                f"Illegal transition {current.value} -> {new_state.value} for {doc_id}"
            )
        cur = self._conn.cursor()
        cur.execute(
            f"UPDATE documents SET state = {self._ph}, error_message = {self._ph}, "
            f"updated_at = CURRENT_TIMESTAMP WHERE doc_id = {self._ph}",
            (new_state.value, error_message, doc_id),
        )
        self._conn.commit()
        logger.info("Document %s: %s -> %s", doc_id, current.value, new_state.value)

    def find_by_hash(self, content_hash: str) -> str | None:
        """Return the doc_id already holding this content hash, if any (dedup)."""
        cur = self._conn.cursor()
        cur.execute(
            f"SELECT doc_id FROM documents WHERE content_hash = {self._ph}",
            (content_hash,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    def close(self) -> None:
        self._conn.close()


def get_repository(database_url: str | None = None) -> MetadataRepository:
    """Build a repository from a SQLAlchemy-style URL.

    ``postgresql://...`` / ``postgres://...`` -> psycopg2 (placeholder ``%s``)
    ``sqlite:///path``                        -> sqlite3 (placeholder ``?``)
    """
    url = database_url or settings.database_url
    if url.startswith(("postgresql://", "postgres://")):
        import psycopg2

        conn = psycopg2.connect(url)
        return MetadataRepository(conn, placeholder="%s")

    if url.startswith("sqlite:///"):
        import os
        import sqlite3

        path = url[len("sqlite:///"):]
        if path and os.path.dirname(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path or ":memory:")
        return MetadataRepository(conn, placeholder="?")

    raise ValueError(f"Unsupported DATABASE_URL scheme: {url!r}")

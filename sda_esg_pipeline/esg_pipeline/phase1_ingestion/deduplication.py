"""News deduplication (SDAD §3.4).

Vietnamese text can be typed with two different Unicode encodings of the same
diacritic, which would otherwise hash differently. We NFC-normalise (and strip
whitespace) before SHA-256 so logically-identical content collapses to one hash.
State lives in a SQLite checkpoint DB.
"""
from __future__ import annotations

import hashlib
import sqlite3
import unicodedata

DEFAULT_DB_PATH = "./data/checkpoint.db"


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    """Create the ``news_hashes`` table if it does not yet exist."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS news_hashes (
                content_hash TEXT PRIMARY KEY,
                title        TEXT,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()


def generate_content_hash(text: str) -> str:
    """NFC-normalise + lowercase + drop all whitespace, then SHA-256 hex."""
    normalized = unicodedata.normalize("NFC", text.lower())
    cleaned = "".join(normalized.split())
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def check_and_save_news(news_item: dict, db_path: str = DEFAULT_DB_PATH) -> bool:
    """Insert a news item if unseen.

    Returns True when newly inserted (caller should process it), False when a
    duplicate already exists.
    """
    content_hash = generate_content_hash(news_item["body_text"])
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        exists = cursor.execute(
            "SELECT 1 FROM news_hashes WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        if exists:
            return False
        cursor.execute(
            "INSERT INTO news_hashes (content_hash, title) VALUES (?, ?)",
            (content_hash, news_item.get("title", "")),
        )
        conn.commit()
        return True

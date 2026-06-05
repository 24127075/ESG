"""Phase 1 + common-layer unit tests — all run offline (no Redis/DB/network).

    pytest -q
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.common.exceptions import QuotaExceededException  # noqa: E402
from esg_pipeline.common.security import safe_filename, safe_join  # noqa: E402
from esg_pipeline.common.state import DocumentState, can_transition  # noqa: E402
from esg_pipeline.phase1_ingestion.crawler_tier2 import (  # noqa: E402
    _pick_report_url,
    is_trusted_domain,
)
from esg_pipeline.phase1_ingestion.deduplication import generate_content_hash  # noqa: E402
from esg_pipeline.phase1_ingestion.downloader import looks_like_pdf, sha256_bytes  # noqa: E402
from esg_pipeline.phase1_ingestion.metadata_repo import MetadataRepository  # noqa: E402
from esg_pipeline.phase1_ingestion.rate_limiter import RateLimiter  # noqa: E402


# ── A tiny in-memory Redis double (only the ops the limiter uses) ────────────
class FakeRedis:
    def __init__(self):
        self.store: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True


# ── security ──────────────────────────────────────────────────────────────
def test_safe_filename_strips_traversal():
    assert "/" not in safe_filename("../../etc/passwd")
    assert ".." not in safe_filename("../../etc/passwd")


def test_safe_join_blocks_escape(tmp_path):
    assert safe_join(str(tmp_path), "reports", "a.pdf").startswith(str(tmp_path))
    with pytest.raises(ValueError):
        safe_join(str(tmp_path), "../../etc/passwd")


# ── state machine ───────────────────────────────────────────────────────────
def test_state_transitions():
    assert can_transition(DocumentState.PENDING, DocumentState.DOWNLOADED)
    assert can_transition(DocumentState.EXTRACTED, DocumentState.CLEANED)  # skip OCR
    assert can_transition(DocumentState.CLEANED, DocumentState.ERROR)
    assert not can_transition(DocumentState.COMPLETED, DocumentState.PENDING)
    assert not can_transition(DocumentState.ERROR, DocumentState.CHUNKED)


# ── dedup hashing (NFC) ─────────────────────────────────────────────────────
def test_nfc_hash_is_stable_across_unicode_forms():
    import unicodedata

    nfc = unicodedata.normalize("NFC", "Phát thải khí nhà kính")
    nfd = unicodedata.normalize("NFD", "Phát thải khí nhà kính")
    assert nfc != nfd  # different byte sequences...
    assert generate_content_hash(nfc) == generate_content_hash(nfd)  # ...same hash


# ── trusted-domain filter (§3.2) ────────────────────────────────────────────
def test_trusted_domain_filter():
    assert is_trusted_domain("https://vinamilk.com.vn/report.pdf", "vinamilk.com.vn")
    assert is_trusted_domain("https://abc.vn/x.pdf", "other.com")  # VN TLD ok
    assert not is_trusted_domain("https://scribd.com/x.pdf", "vinamilk.com.vn")
    assert not is_trusted_domain("https://123doc.vn/x.pdf", "vinamilk.com.vn")


# ── DuckDuckGo result picking (§3.2, offline/pure) ──────────────────────────
def test_pick_report_url_prefers_trusted_pdf_and_drops_piracy():
    results = [
        {"href": "https://scribd.com/vnm.pdf"},          # piracy → drop
        {"href": "https://news.vn/article"},             # not a pdf
        {"href": "https://ir.vinamilk.com.vn/bctn.pdf"},  # trusted pdf → pick
        {"href": "https://random.org/x.pdf"},            # non-trusted pdf
    ]
    assert _pick_report_url(results, "vinamilk.com.vn") == "https://ir.vinamilk.com.vn/bctn.pdf"
    # No trusted hit → best-effort non-piracy pdf.
    assert _pick_report_url(
        [{"href": "https://123doc.vn/a.pdf"}, {"href": "https://fpt.com/r.pdf"}],
        "fpt.com.vn",
    ) == "https://fpt.com/r.pdf"
    # Nothing usable.
    assert _pick_report_url([{"href": "https://scribd.com/a.pdf"}], "x.com.vn") is None


# ── rate limiter + quota guard ──────────────────────────────────────────────
def test_rate_limit_window():
    limiter = RateLimiter(FakeRedis())
    results = [limiter.check_rate_limit("k", limit=3, window_seconds=60) for _ in range(5)]
    assert results == [True, True, True, False, False]


def test_quota_raises_when_exhausted():
    limiter = RateLimiter(FakeRedis())
    for _ in range(100):
        limiter.consume_quota("google_cse", daily_quota=100)
    with pytest.raises(QuotaExceededException):
        limiter.consume_quota("google_cse", daily_quota=100)


# ── downloader helpers ──────────────────────────────────────────────────────
def test_pdf_magic_and_hash():
    assert looks_like_pdf(b"%PDF-1.7\n...")
    assert not looks_like_pdf(b"<html>")
    assert len(sha256_bytes(b"abc")) == 64


# ── metadata repo (sqlite in-memory) + state machine persistence ─────────────
def test_metadata_repo_lifecycle():
    import sqlite3

    repo = MetadataRepository(sqlite3.connect(":memory:"), placeholder="?")
    doc_id = repo.upsert_document("VNM", 2023, content_hash="deadbeef")
    assert repo.get_state(doc_id) == DocumentState.PENDING
    assert repo.find_by_hash("deadbeef") == doc_id

    repo.transition(doc_id, DocumentState.DOWNLOADED)
    assert repo.get_state(doc_id) == DocumentState.DOWNLOADED

    with pytest.raises(ValueError):  # illegal backward move
        repo.transition(doc_id, DocumentState.PENDING)

"""Document downloader (SDAD §1 step 4).

Downloads a candidate file, scans it for malware (ClamAV), and computes a
SHA-256 digest used for content de-duplication before anything is persisted.
"""
from __future__ import annotations

import hashlib
import logging

from ..common.exceptions import PDFCorruptedException

logger = logging.getLogger(__name__)

DOWNLOAD_TIMEOUT = 60
MAX_BYTES = 100 * 1024 * 1024  # 100 MB guard
_PDF_MAGIC = b"%PDF-"

# Many corporate IR/disclosure servers reject the default ``python-requests``
# User-Agent with HTTP 403. Present as a normal browser. (Production: rotate UAs
# / honour robots.txt as policy dictates.)
_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,application/octet-stream,*/*",
    "Accept-Language": "vi,en;q=0.8",
}


def sha256_bytes(content: bytes) -> str:
    """SHA-256 hex digest of raw file bytes (binary de-duplication key)."""
    return hashlib.sha256(content).hexdigest()


def scan_for_malware(content: bytes) -> bool:
    """Scan bytes with ClamAV. Returns True if clean.

    If the ClamAV daemon / ``clamd`` package is unavailable we fail OPEN with a
    loud warning (so local dev isn't blocked), but log it as a gap — Production
    runs the scan inside the sandboxed container (§4.1).
    """
    try:
        import clamd
    except ImportError:
        logger.warning("clamd not installed — skipping malware scan (NOT for prod).")
        return True

    try:
        scanner = clamd.ClamdUnixSocket()
        result = scanner.instream(__import__("io").BytesIO(content))
        status = result.get("stream", ("ERROR",))[0]
        if status == "FOUND":
            logger.error("Malware detected by ClamAV: %s", result["stream"])
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - daemon down etc.
        logger.warning("ClamAV scan unavailable (%s) — failing open.", exc)
        return True


def looks_like_pdf(content: bytes) -> bool:
    """Cheap structural sanity check on the file header."""
    return content[:5] == _PDF_MAGIC


def download_document(url: str, expect_pdf: bool = True) -> tuple[bytes, str]:
    """Download ``url``, scan it, and return ``(content, sha256_hex)``.

    Raises :class:`PDFCorruptedException` if the download is too large, fails
    the malware scan, or (when ``expect_pdf``) is not a valid PDF.
    """
    import requests

    resp = requests.get(
        url, timeout=DOWNLOAD_TIMEOUT, stream=True,
        headers=_DEFAULT_HEADERS, allow_redirects=True,
    )
    resp.raise_for_status()

    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=64 * 1024):
        total += len(chunk)
        if total > MAX_BYTES:
            raise PDFCorruptedException(f"Download exceeded {MAX_BYTES} bytes: {url}")
        chunks.append(chunk)
    content = b"".join(chunks)

    if not scan_for_malware(content):
        raise PDFCorruptedException(f"Malware detected in download: {url}")
    if expect_pdf and not looks_like_pdf(content):
        raise PDFCorruptedException(f"Downloaded file is not a valid PDF: {url}")

    return content, sha256_bytes(content)

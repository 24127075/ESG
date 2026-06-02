"""Text & table extraction heuristics (SDAD §7).

Strategy:
  * Tables: Camelot (lattice) first; pdfplumber as fallback. Tables are
    flattened to ``"<header>: <cell> | ..."`` sentences so they survive
    chunking as natural language.
  * Text-based PDFs: PyMuPDF (``fitz``) page text + flattened tables.
  * Scanned (raster) PDFs: render at 300 DPI and OCR with Tesseract (vie),
    keeping only words with OCR confidence > 60 to drop noise.
"""
from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

OCR_CONFIDENCE_THRESHOLD = 60
CAMELOT_ACCURACY_THRESHOLD = 80
OCR_DPI = 300


def extract_tables_robust(pdf_path: str, is_scanned: bool) -> list[str]:
    """Return tables flattened to one sentence per row.

    Raster/scanned PDFs are skipped here — standard OCR cannot reconstruct
    grid structure reliably (they would need a vision model).
    """
    # Raster PDFs require vision models; standard OCR fails on tables.
    if is_scanned:
        return []

    import camelot
    import pdfplumber

    tables_as_text: list[str] = []
    try:
        # Priority 1: Camelot for grid/lattice-based tables.
        tables = camelot.read_pdf(
            pdf_path, pages="all", flavor="lattice", line_scale=40
        )
        valid_tables = [t.df for t in tables if t.accuracy > CAMELOT_ACCURACY_THRESHOLD]
        if not valid_tables:
            raise ValueError("Camelot failed or low accuracy")

        for df in valid_tables:
            # Heuristic: drop tables with fewer than 2 columns.
            if len(df.columns) < 2:
                continue
            headers = [str(c).strip() for c in df.iloc[0]]
            for _, row in df.iloc[1:].iterrows():
                cells = [str(c).strip() for c in row]
                sentence = " | ".join(
                    f"{h}: {c}" for h, c in zip(headers, cells) if c
                )
                if sentence:
                    tables_as_text.append(sentence)
    except Exception:  # noqa: BLE001 - Camelot raises many concrete types
        # Priority 2: fall back to pdfplumber.
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                for table in page.extract_tables():
                    if not table or len(table[0]) < 2:
                        continue
                    # Heuristic: drop navigation menus masquerading as tables.
                    if "Page" in str(table[0]):
                        continue
                    headers = [str(cell or "").strip() for cell in table[0]]
                    for row in table[1:]:
                        cells = [str(cell or "").strip() for cell in row]
                        sentence = " | ".join(
                            f"{h}: {c}" for h, c in zip(headers, cells) if c
                        )
                        if sentence:
                            tables_as_text.append(sentence)

    return tables_as_text


def _extract_text_based(pdf_path: str) -> dict:
    """Extract a digitally-born (non-scanned) PDF.

    Returns ``{"raw_text": str, "tables": list[str]}``. Body text comes from
    PyMuPDF; tables are flattened via :func:`extract_tables_robust` and appended
    so the taxonomy stage can see tabular ESG figures too.
    """
    import fitz  # PyMuPDF

    raw_text_pages: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            raw_text_pages.append(page.get_text("text"))
    finally:
        doc.close()

    tables = extract_tables_robust(pdf_path, is_scanned=False)
    return {"raw_text": "\n".join(raw_text_pages), "tables": tables}


def _extract_scan_based(pdf_path: str) -> dict:
    """OCR a scanned/raster PDF (Tesseract, Vietnamese), filtering by confidence."""
    import fitz
    import pytesseract
    from PIL import Image
    from pytesseract import Output

    raw_text_pages: list[str] = []
    doc = fitz.open(pdf_path)
    try:
        for page in doc:
            mat = fitz.Matrix(OCR_DPI / 72, OCR_DPI / 72)
            pix = page.get_pixmap(matrix=mat)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            # image_to_data lets us filter words by per-word confidence score.
            data = pytesseract.image_to_data(img, lang="vie", output_type=Output.DICT)
            valid_words = [
                data["text"][i]
                for i in range(len(data["text"]))
                if _safe_conf(data["conf"][i]) > OCR_CONFIDENCE_THRESHOLD
                and data["text"][i].strip()
            ]
            raw_text_pages.append(" ".join(valid_words))
    finally:
        doc.close()

    return {"raw_text": "\n".join(raw_text_pages), "tables": []}


def _safe_conf(value) -> int:
    """Tesseract reports conf as '-1' for non-text blocks; coerce safely."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return -1


def extract_document(pdf_path: str, is_scanned: bool) -> dict:
    """Dispatch to the scan-based or text-based extractor."""
    return _extract_scan_based(pdf_path) if is_scanned else _extract_text_based(pdf_path)

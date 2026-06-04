#!/usr/bin/env python3
"""Run the full Phase 2 pipeline (SDAD §6-§10) on a REAL PDF → JSONL.

Three input modes:

    # 1) A local PDF you already have:
    python scripts/run_phase2_pdf.py --pdf path/to/report.pdf --ticker VNM --year 2023

    # 2) Download a real report by URL first (malware-scanned, §1 downloader):
    python scripts/run_phase2_pdf.py --url https://.../baocao.pdf --ticker FPT --year 2023

    # 3) No input → generate a representative Vietnamese ESG PDF and run on it
    #    (fully offline, proves the PDF→extract→clean→chunk→tag→JSONL path):
    python scripts/run_phase2_pdf.py

Add ``--scanned`` to force the OCR (Tesseract) path for raster PDFs.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.common.logging_config import ensure_utf8_io  # noqa: E402
from esg_pipeline.phase2_preprocessing.orchestrator import (  # noqa: E402
    process_single_document,
)

# No-diacritic Vietnamese ESG text (matches the taxonomy + avoids base-14 font
# glyph gaps when synthesising a PDF). Headings are numbered so §8 chunking
# picks them up as heading_context.
_SAMPLE_PARAGRAPHS = [
    "1. Bao cao Moi truong",
    "Cong ty cam ket giam phat thai khi nha kinh va huong toi net zero vao nam 2050. "
    "Tong luong phat thai CO2 nam 2023 dat 1500 tan, giam 12 phan tram so voi nam truoc "
    "nho su dung nang luong tai tao va kinh te tuan hoan.",
    "Hoat dong quan ly chat thai va tieu thu nuoc duoc giam sat chat che tai cac nha may.",
    "2. Bao cao Xa hoi",
    "Cong ty chu trong an toan lao dong; khong co tai nan lao dong nghiem trong trong nam. "
    "Cac chuong trinh phuc loi nhan vien va binh dang gioi tiep tuc duoc mo rong.",
    "3. Quan tri",
    "Hoi dong quan tri co thanh vien doc lap; bao cao duoc kiem toan doc lap va cam ket "
    "minh bach, chong tham nhung.",
]


def _make_sample_pdf(path: str) -> None:
    """Synthesise a small text-based Vietnamese ESG PDF with PyMuPDF (fitz).

    Each paragraph goes on its own page so that, once the extractor joins pages
    with a blank line, §8 cleaning sees them as distinct paragraphs (headings vs
    body) instead of one merged block.
    """
    import fitz

    doc = fitz.open()
    for para in _SAMPLE_PARAGRAPHS:
        page = doc.new_page()
        page.insert_textbox(fitz.Rect(72, 72, 540, 720), para, fontsize=11, fontname="helv")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    doc.save(path)
    doc.close()


def _download(url: str, dest: str) -> None:
    """Download + malware-scan a real report via the §1 downloader."""
    from esg_pipeline.phase1_ingestion.downloader import download_document

    content, digest = download_document(url, expect_pdf=True)
    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "wb") as f:
        f.write(content)
    print(f"Downloaded {len(content)} bytes (sha256={digest[:16]}…) → {dest}")


def main() -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", help="Path to an existing local PDF")
    parser.add_argument("--url", help="URL of a real report PDF to download first")
    parser.add_argument("--ticker", default="DEMO")
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument("--scanned", action="store_true", help="Force the OCR path")
    parser.add_argument("--out", default="./out/phase2")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    pdf_path = args.pdf

    if args.url:
        pdf_path = os.path.join(args.out, f"{args.ticker}_{args.year}.pdf")
        _download(args.url, pdf_path)
    elif not pdf_path:
        pdf_path = os.path.join(args.out, f"{args.ticker}_{args.year}_sample.pdf")
        _make_sample_pdf(pdf_path)
        print(f"No input given — generated a sample ESG PDF → {pdf_path}")

    meta = {
        "absolute_storage_path": os.path.abspath(pdf_path),
        "ticker": args.ticker,
        "fiscal_year": args.year,
        "pdf_type_flag": "SCAN_BASED" if args.scanned else "TEXT_BASED",
        "heading_context": "Thong tin chung",
        "normalize_ocr": args.scanned,
    }
    meta_path = os.path.join(args.out, f"{args.ticker}_{args.year}_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    n = process_single_document(meta_path, args.out)
    out_jsonl = os.path.join(args.out, f"{args.ticker}_{args.year}_chunks.jsonl")
    print(f"\nWrote {n} ESG-relevant chunks → {out_jsonl}\n")
    if os.path.exists(out_jsonl):
        with open(out_jsonl, "r", encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                print(f"  [{','.join(rec['matched_tags'])}] "
                      f"({rec['heading_context']}) {rec['filtered_chunk_text'][:90]}…")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    sys.exit(main())

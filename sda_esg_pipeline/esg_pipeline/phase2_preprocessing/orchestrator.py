"""Phase 2 orchestration & JSONL I/O (SDAD §10).

Ties the modules together for one document:

    metadata.json
        -> extraction (text- or scan-based) + flattened tables
        -> cleaning & normalization
        -> semantic chunking (<= 200 tokens)
        -> ESG taxonomy tagging (keep only chunks with >= 1 matched tag)
        -> JSONL ({ticker}_{year}_chunks.jsonl)

The emitted record schema matches the SDAD §10 output example:
    ticker, fiscal_year, heading_context, filtered_chunk_text,
    token_count, matched_tags, chunk_source
"""
from __future__ import annotations

import json
import logging
import os

from .cleaning import chunk_document, clean_text_advanced, count_tokens
from .extraction import extract_document
from .taxonomy import TaxonomyTagger

logger = logging.getLogger(__name__)

DEFAULT_TAXONOMY_PATH = os.path.join("config", "esg_taxonomy.json")


def _build_records(
    extracted: dict,
    ticker: str,
    fiscal_year: int,
    tagger: TaxonomyTagger,
) -> list[dict]:
    """Clean -> chunk -> tag; return only ESG-relevant records."""
    records: list[dict] = []

    # Body text → semantic chunks (chunk_source = TEXT).
    paragraphs = clean_text_advanced(extracted.get("raw_text", ""))
    for chunk in chunk_document(paragraphs):
        tags = tagger.tag(chunk["text"])
        if tags:
            records.append(
                {
                    "ticker": ticker,
                    "fiscal_year": fiscal_year,
                    "heading_context": chunk["heading"],
                    "filtered_chunk_text": chunk["text"],
                    "token_count": chunk["token_count"],
                    "matched_tags": tags,
                    "chunk_source": "TEXT",
                }
            )

    # Flattened table rows → one candidate chunk each (chunk_source = TABLE).
    for sentence in extracted.get("tables", []):
        tags = tagger.tag(sentence)
        if tags:
            records.append(
                {
                    "ticker": ticker,
                    "fiscal_year": fiscal_year,
                    "heading_context": "Bang du lieu",
                    "filtered_chunk_text": sentence,
                    "token_count": count_tokens(sentence),
                    "matched_tags": tags,
                    "chunk_source": "TABLE",
                }
            )

    return records


def process_single_document(
    metadata_path: str,
    output_dir: str,
    taxonomy_path: str = DEFAULT_TAXONOMY_PATH,
) -> int:
    """Run the full Phase 2 pipeline for one document.

    ``metadata.json`` must contain: ``absolute_storage_path``, ``ticker``,
    ``fiscal_year``, ``pdf_type_flag`` ("SCAN_BASED" | "TEXT_BASED").

    Returns the number of ESG-relevant chunks written.
    """
    with open(metadata_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    pdf_path = meta["absolute_storage_path"]
    ticker = meta["ticker"]
    year = meta["fiscal_year"]
    is_scanned = meta["pdf_type_flag"] == "SCAN_BASED"

    extracted = extract_document(pdf_path, is_scanned)
    tagger = TaxonomyTagger(taxonomy_path)
    records = _build_records(extracted, ticker, year, tagger)

    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{ticker}_{year}_chunks.jsonl")
    with open(output_path, "w", encoding="utf-8") as f:
        for record in records:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    logger.info("Wrote %d ESG chunks → %s", len(records), output_path)
    return len(records)


def process_raw_text(
    raw_text: str,
    ticker: str,
    fiscal_year: int,
    taxonomy_path: str = DEFAULT_TAXONOMY_PATH,
) -> list[dict]:
    """Convenience path for the SDAD §10 raw-OCR-text demo (no PDF on disk)."""
    tagger = TaxonomyTagger(taxonomy_path)
    return _build_records({"raw_text": raw_text, "tables": []}, ticker, fiscal_year, tagger)

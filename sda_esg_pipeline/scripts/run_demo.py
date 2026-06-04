#!/usr/bin/env python3
"""Phase 2 end-to-end demo (SDAD §10 I/O example).

Feeds the documented raw OCR text through cleaning -> chunking -> taxonomy
tagging and prints the resulting JSONL records. Runs offline: if PhoBERT /
pyahocorasick are not installed it transparently uses the built-in fallbacks.

Usage:
    python scripts/run_demo.py
"""
from __future__ import annotations

import json
import os
import sys

# Make the package importable when run straight from a checkout.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.phase2_preprocessing.orchestrator import process_raw_text  # noqa: E402

# Input (raw OCR text) — verbatim from the SDAD §10 demo, including the
# leet/dropped-vowel OCR noise ("Cng", "N3t Zer0"). normalize_ocr repairs it.
SAMPLE_INPUT = {
    "raw_text": (
        "Cng ty huong toi N3t Zer0.\n\n"
        "Tong luong phat thai CO2 nam 2023 la 1500 tan."
    )
}

TAXONOMY_PATH = os.path.join(
    os.path.dirname(__file__), "..", "config", "esg_taxonomy.json"
)


def main() -> None:
    records = process_raw_text(
        SAMPLE_INPUT["raw_text"],
        ticker="VNM",
        fiscal_year=2023,
        taxonomy_path=TAXONOMY_PATH,
        heading_context="Bao cao Moi truong",
        normalize_ocr=True,
    )

    print("Input (raw OCR text):")
    print(json.dumps(SAMPLE_INPUT, ensure_ascii=False, indent=2))
    print("\nOutput (tagged JSONL chunks):")
    if not records:
        print("  <no ESG-relevant chunks matched>")
    for record in records:
        print(json.dumps(record, ensure_ascii=False))


if __name__ == "__main__":
    main()

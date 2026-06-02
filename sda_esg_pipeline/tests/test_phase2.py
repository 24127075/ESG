"""Phase 2 unit tests — run offline using the built-in fallbacks.

    pytest -q
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.phase2_preprocessing.cleaning import (  # noqa: E402
    chunk_document,
    clean_text_advanced,
    is_heading,
)
from esg_pipeline.phase2_preprocessing.orchestrator import process_raw_text  # noqa: E402
from esg_pipeline.phase2_preprocessing.taxonomy import TaxonomyTagger  # noqa: E402

CONFIG = os.path.join(os.path.dirname(__file__), "..", "config", "esg_taxonomy.json")


# ── cleaning ────────────────────────────────────────────────────────────────
def test_clean_joins_intra_sentence_newlines_and_drops_page_noise():
    raw = "Cau bi ngat\ndong giua.\n\nDoan hai.\nTrang 12 / 100"
    paras = clean_text_advanced(raw)
    assert "Cau bi ngat dong giua." in paras
    assert "Doan hai." in paras
    assert all("Trang 12" not in p for p in paras)


def test_is_heading_detects_numbered_sections():
    assert is_heading("1. Tong quan")
    assert is_heading("2.3 Co che du phong")
    assert is_heading("III. Phan ket")
    assert not is_heading("Cong ty huong toi Net Zero.")


def test_chunk_respects_max_tokens_and_heading_context():
    paragraphs = ["1. Moi truong"] + [f"Cau so {i} ve phat thai." for i in range(60)]
    chunks = chunk_document(paragraphs)
    assert chunks, "expected at least one chunk"
    assert all(c["token_count"] <= 200 for c in chunks)
    assert chunks[0]["heading"] == "1. Moi truong"


# ── taxonomy ────────────────────────────────────────────────────────────────
def test_taxonomy_weight_threshold_and_esg_ordering():
    tagger = TaxonomyTagger(CONFIG)
    # "net zero" (w=2) + "co2" (w=1) → E_Emissions total 3 >= threshold 2.
    tags = tagger.tag("Cong ty huong toi net zero, giam co2.")
    assert "E_Emissions" in tags

    # A single weight-1 keyword stays below the default threshold of 2.
    assert tagger.tag("Bao cao co minh bach.") == []


# ── orchestration (the §10 I/O demo) ─────────────────────────────────────────
def test_demo_produces_expected_emissions_chunk():
    raw = "Cong ty huong toi Net Zero.\n\nTong luong phat thai CO2 nam 2023 la 1500 tan."
    records = process_raw_text(raw, ticker="VNM", fiscal_year=2023, taxonomy_path=CONFIG)
    assert len(records) == 1
    rec = records[0]
    assert rec["ticker"] == "VNM"
    assert rec["fiscal_year"] == 2023
    assert rec["matched_tags"] == ["E_Emissions"]
    assert rec["chunk_source"] == "TEXT"
    assert "CO2" in rec["filtered_chunk_text"]  # chemical figure preserved
    # token_count is populated and within the PhoBERT-safe bound.
    assert 0 < rec["token_count"] <= 200

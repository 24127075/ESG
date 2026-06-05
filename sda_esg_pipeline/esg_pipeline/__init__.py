"""SDA ESG Quantitative Model — Data Ingestion & Preprocessing pipeline.

Implements the architecture described in *SDA-Data-Ingestion-Preprocessing-v3*:

    Phase 1 (esg_pipeline.phase1_ingestion)
        - Quantitative engine (vnstock → Fama-French 6 factor inputs)
        - 3-tier unstructured crawler (HOSE/HNX, Google CSE, RSS)
        - News deduplication (NFC + SHA-256)

    Phase 2 (esg_pipeline.phase2_preprocessing)
        - Text & table extraction (PyMuPDF / Camelot / pdfplumber / Tesseract)
        - Cleaning & semantic chunking (PhoBERT-bounded, <= 200 tokens)
        - ESG taxonomy mapping (Aho-Corasick, O(n))
        - Orchestration → JSONL

    Common (esg_pipeline.common)
        - Exception hierarchy, document state machine, monitoring, security.
"""

__version__ = "3.0.0"

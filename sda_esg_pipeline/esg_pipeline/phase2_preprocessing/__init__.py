"""Phase 2 — Preprocessing & Semantic Chunking.

Pipeline: Raw PDF/JSON -> extraction (PyMuPDF/Camelot/pdfplumber, Tesseract if
scanned) -> cleaning & normalization -> semantic chunking (<= 200 tokens) ->
taxonomy mapping (Aho-Corasick) -> JSONL. Prepares clean inputs for the
PhoBERT NLP model [Nguyen & Nguyen 2020].
"""

"""Phase 1 — Data Ingestion Pipeline.

Sub-modules:
    quantitative   : vnstock → Fama-French 6 factor inputs (+ retry/CSV fallback)
    crawler_tier1  : HOSE/HNX mandatory-disclosure DOM scraping
    crawler_tier2  : Google CSE automation (quota guard, circuit breaker, cache)
    crawler_tier3  : RSS financial-news aggregator
    deduplication  : NFC-normalised SHA-256 content de-duplication
"""

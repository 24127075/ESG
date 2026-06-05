#!/usr/bin/env python3
"""Batch ESG-report crawl over the whole ticker universe (SDAD §3.2 + §6-§10).

Reads the Fama-French universe CSV (``ff_universe.csv``, one ``ticker`` column)
and, for every ticker x fiscal-year, runs the real Phase 1 -> Phase 2 path:

    DuckDuckGo search (free, no key)  ->  download + malware scan + SHA-256
        ->  Phase 2 extract / clean / chunk / ESG-tag  ->  {TICKER}_{YEAR}_chunks.jsonl

It is built for a long, unattended run over ~311 tickers x 5 years (~1.5k jobs):

  * **Resumable** — a ``_manifest.csv`` records every outcome; re-running skips
    combos already finished (use ``--retry-failed`` to re-attempt non-successes).
  * **Rate-limit aware** — a polite delay between searches, plus exponential
    back-off when DuckDuckGo trips the circuit breaker (QuotaExceededException).
  * **Fault-tolerant** — any single ticker/year failure is logged and skipped;
    the batch keeps going. Ctrl-C stops cleanly with the manifest preserved.

Examples::

    # Whole universe, 2020-2024 (the intended run):
    python scripts/crawl_esg_universe.py --years 2020-2024

    # Smoke-test on the first 5 tickers, one year:
    python scripts/crawl_esg_universe.py --years 2023 --limit 5

    # Point at a CSV elsewhere and slow the searches down:
    python scripts/crawl_esg_universe.py --universe ../ff_universe.csv --delay 8
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from esg_pipeline.common.exceptions import QuotaExceededException  # noqa: E402
from esg_pipeline.common.logging_config import ensure_utf8_io  # noqa: E402
from esg_pipeline.phase1_ingestion.crawler_tier2 import search_esg_report  # noqa: E402
from esg_pipeline.phase1_ingestion.downloader import download_document  # noqa: E402
from esg_pipeline.phase2_preprocessing.orchestrator import (  # noqa: E402
    process_single_document,
)

# Terminal manifest statuses (a finished combo). On a normal resume we skip all
# of these; with --retry-failed we only keep the genuine successes.
_SUCCESS = {"processed"}
_TERMINAL = {"processed", "no_chunks", "not_found", "download_failed", "search_error"}

_MANIFEST_FIELDS = ["ticker", "year", "status", "url", "n_chunks", "error"]


def _default_universe_path() -> str:
    """Find ff_universe.csv: repo root (../../) first, then a couple of fallbacks."""
    here = os.path.dirname(__file__)
    for rel in ("../../ff_universe.csv", "../ff_universe.csv", "./ff_universe.csv"):
        candidate = os.path.abspath(os.path.join(here, rel))
        if os.path.exists(candidate):
            return candidate
    return os.path.abspath(os.path.join(here, "../../ff_universe.csv"))


def _read_tickers(path: str) -> list[str]:
    """Read the ``ticker`` column (case-insensitive header) from the universe CSV."""
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return []
    header = [c.strip().lower() for c in rows[0]]
    col = header.index("ticker") if "ticker" in header else 0
    seen, tickers = set(), []
    start = 1 if "ticker" in header else 0
    for row in rows[start:]:
        if not row or col >= len(row):
            continue
        t = row[col].strip().upper()
        if t and t not in seen:
            seen.add(t)
            tickers.append(t)
    return tickers


def _parse_years(spec: str) -> list[int]:
    """Parse '2020-2024' or '2020,2022,2024' (or a mix) into a sorted year list."""
    years: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = (int(x) for x in part.split("-", 1))
            years.update(range(lo, hi + 1))
        else:
            years.add(int(part))
    return sorted(years)


def _load_manifest(path: str) -> dict[tuple[str, int], str]:
    """Map (ticker, year) -> last recorded status from an existing manifest."""
    done: dict[tuple[str, int], str] = {}
    if not os.path.exists(path):
        return done
    with open(path, "r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            try:
                done[(row["ticker"], int(row["year"]))] = row["status"]
            except (KeyError, ValueError):
                continue
    return done


def _open_manifest(path: str):
    """Open the manifest for appending, writing the header if it's new."""
    is_new = not os.path.exists(path) or os.path.getsize(path) == 0
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    f = open(path, "a", encoding="utf-8", newline="")
    writer = csv.DictWriter(f, fieldnames=_MANIFEST_FIELDS)
    if is_new:
        writer.writeheader()
        f.flush()
    return f, writer


def _official_domain(ticker: str) -> str:
    """Issuer IR domain heuristic (matches scheduler.tasks fallback)."""
    return f"{ticker.lower()}.com.vn"


def _process_one(
    ticker: str, year: int, out_dir: str, scanned: bool, max_results: int,
    keep_pdf: bool = True,
) -> dict:
    """Search -> download -> Phase 2 for one ticker/year. Returns a manifest row.

    Raises QuotaExceededException upward so the caller can back off; all other
    failures are captured into the returned row so the batch continues. When
    ``keep_pdf`` is False the downloaded PDF is deleted after extraction (the
    JSONL is the deliverable) — handy on disk-capped runners like Kaggle.
    """
    reports_dir = os.path.join(out_dir, "reports", ticker)
    phase2_dir = os.path.join(out_dir, "phase2")
    row = {"ticker": ticker, "year": year, "url": "", "n_chunks": 0, "error": ""}

    url = search_esg_report(ticker, year, _official_domain(ticker), max_results=max_results)
    if not url:
        row["status"] = "not_found"
        return row
    row["url"] = url

    try:
        content, digest = download_document(url, expect_pdf=True)
    except Exception as exc:  # noqa: BLE001 - report and continue
        row["status"] = "download_failed"
        row["error"] = f"{type(exc).__name__}: {exc}"
        return row

    os.makedirs(reports_dir, exist_ok=True)
    pdf_path = os.path.join(reports_dir, f"{year}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(content)

    meta_path = os.path.join(phase2_dir, f"{ticker}_{year}_meta.json")
    os.makedirs(phase2_dir, exist_ok=True)
    import json

    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "absolute_storage_path": os.path.abspath(pdf_path),
                "ticker": ticker,
                "fiscal_year": year,
                "pdf_type_flag": "SCAN_BASED" if scanned else "TEXT_BASED",
                "heading_context": "Thong tin chung",
                "normalize_ocr": scanned,
                "content_sha256": digest,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    try:
        n = process_single_document(meta_path, phase2_dir)
    except Exception as exc:  # noqa: BLE001
        row["status"] = "search_error"
        row["error"] = f"phase2 {type(exc).__name__}: {exc}"
        return row
    finally:
        if not keep_pdf:
            try:
                os.remove(pdf_path)
            except OSError:
                pass

    row["n_chunks"] = n
    row["status"] = "processed" if n > 0 else "no_chunks"
    return row


def main() -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--universe", default=_default_universe_path(),
                        help="Path to ff_universe.csv (default: repo-root file)")
    parser.add_argument("--years", default="2020-2024",
                        help="Years: range '2020-2024' or list '2020,2022' (default 2020-2024)")
    parser.add_argument("--out", default="./out", help="Output base dir (default ./out)")
    parser.add_argument("--delay", type=float, default=5.0,
                        help="Polite seconds between searches (default 5)")
    parser.add_argument("--limit", type=int, default=0,
                        help="Process only the first N tickers (0 = all)")
    parser.add_argument("--max-results", type=int, default=10,
                        help="DuckDuckGo results per search (default 10)")
    parser.add_argument("--scanned", action="store_true",
                        help="Force the OCR path for all reports")
    parser.add_argument("--no-keep-pdf", dest="keep_pdf", action="store_false",
                        help="Delete each PDF after extraction (save disk on Kaggle)")
    parser.add_argument("--retry-failed", action="store_true",
                        help="On resume, re-attempt combos that were not successful")
    parser.add_argument("--no-resume", action="store_true",
                        help="Ignore the manifest and re-run every combo")
    args = parser.parse_args()

    tickers = _read_tickers(args.universe)
    if not tickers:
        print(f"[ERROR] No tickers read from {args.universe}")
        return 1
    if args.limit > 0:
        tickers = tickers[: args.limit]
    years = _parse_years(args.years)

    manifest_path = os.path.join(args.out, "phase2", "_manifest.csv")
    done = {} if args.no_resume else _load_manifest(manifest_path)
    skip = _SUCCESS if args.retry_failed else _TERMINAL

    total = len(tickers) * len(years)
    print(f"Universe: {len(tickers)} tickers x {len(years)} years = {total} jobs")
    print(f"Years: {years}  |  delay={args.delay}s  |  out={os.path.abspath(args.out)}")
    print(f"Manifest: {manifest_path}\n")

    mf, writer = _open_manifest(manifest_path)
    counts = {"processed": 0, "no_chunks": 0, "not_found": 0,
              "download_failed": 0, "search_error": 0, "skipped": 0}
    backoff = 60.0  # seconds; grows on repeated rate-limit hits
    i = 0
    try:
        for ticker in tickers:
            for year in years:
                i += 1
                tag = f"[{i}/{total}] {ticker} {year}"
                if done.get((ticker, year)) in skip:
                    counts["skipped"] += 1
                    continue

                # Retry the same combo across rate-limit back-offs.
                while True:
                    try:
                        row = _process_one(ticker, year, args.out,
                                            args.scanned, args.max_results,
                                            keep_pdf=args.keep_pdf)
                        break
                    except QuotaExceededException:
                        print(f"{tag}  rate-limited — backing off {backoff:.0f}s…")
                        time.sleep(backoff)
                        backoff = min(backoff * 2, 900)  # cap at 15 min
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:  # noqa: BLE001 - never kill the batch
                        row = {"ticker": ticker, "year": year, "status": "search_error",
                               "url": "", "n_chunks": 0,
                               "error": f"{type(exc).__name__}: {exc}"}
                        break
                else:  # pragma: no cover
                    continue

                backoff = 60.0  # reset after a clean pass
                writer.writerow(row)
                mf.flush()
                counts[row["status"]] = counts.get(row["status"], 0) + 1
                detail = (f"{row['n_chunks']} chunks" if row["status"] == "processed"
                          else row["status"] + (f" ({row['error']})" if row["error"] else ""))
                print(f"{tag}  -> {detail}")

                if year != years[-1] or ticker != tickers[-1]:
                    time.sleep(args.delay)
    except KeyboardInterrupt:
        print("\n[INTERRUPTED] Manifest saved — re-run to resume.")
    finally:
        mf.close()

    print("\n=== Crawl summary ===")
    for k in ("processed", "no_chunks", "not_found",
              "download_failed", "search_error", "skipped"):
        print(f"  {k:16s}: {counts.get(k, 0)}")
    print(f"\nJSONL + manifest under: {os.path.abspath(os.path.join(args.out, 'phase2'))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

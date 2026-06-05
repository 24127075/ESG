#!/usr/bin/env python3
"""Real-data quantitative demo (SDAD §2) using the FREE public ``vnstock``.

Pulls live OHLCV + yearly financials for a few tickers and computes the
Fama-French 6-factor intermediate inputs (operating profit, total assets +
asset growth, shares outstanding, market equity). Writes one CSV per ticker
plus a console summary.

Requires network access (no API key — community tier). Examples::

    python scripts/fetch_quant_demo.py
    python scripts/fetch_quant_demo.py --tickers VNM,FPT,HPG,VCB --start 2023-01-01 --end 2024-12-31
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd  # noqa: E402

from esg_pipeline.common.logging_config import ensure_utf8_io  # noqa: E402
from esg_pipeline.phase1_ingestion.quantitative import fetch_ff6_inputs  # noqa: E402


def _yearly_frame(inputs: dict) -> pd.DataFrame:
    """Tidy yearly accounting frame: operating_profit, total_assets, asset_growth."""
    op = inputs["operating_profit_by_year"]
    ta = inputs["total_assets_by_year"]
    ag = inputs["asset_growth_by_year"]
    years = sorted(set(op) | set(ta))
    return pd.DataFrame(
        [
            {
                "ticker": inputs["symbol"],
                "year": y,
                "operating_profit": op.get(y),
                "total_assets": ta.get(y),
                "asset_growth": ag.get(y),
            }
            for y in years
        ]
    )


def main() -> int:
    ensure_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tickers", default="VNM,FPT,HPG")
    parser.add_argument("--start", default="2023-01-01")
    parser.add_argument("--end", default="2024-12-31")
    parser.add_argument("--out", default="./out/quant")
    args = parser.parse_args()

    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    os.makedirs(args.out, exist_ok=True)

    summaries = []
    for ticker in tickers:
        try:
            inputs = fetch_ff6_inputs(ticker, args.start, args.end)
        except Exception as exc:  # noqa: BLE001 - report and continue
            print(f"[WARN] {ticker}: fetch failed → {exc}")
            continue

        yearly = _yearly_frame(inputs)
        yearly.to_csv(os.path.join(args.out, f"{ticker}_ff6_yearly.csv"), index=False)

        ohlcv = inputs["ohlcv"]
        if ohlcv is not None and not ohlcv.empty:
            ohlcv.to_csv(os.path.join(args.out, f"{ticker}_ohlcv.csv"), index=False)

        last_close = (
            float(ohlcv["close"].iloc[-1]) if ohlcv is not None and not ohlcv.empty else None
        )
        summaries.append(
            {
                "ticker": ticker,
                "shares_outstanding": inputs["shares_outstanding"],
                "last_close": last_close,
                "market_cap_est": (
                    last_close * inputs["shares_outstanding"]
                    if last_close and inputs["shares_outstanding"]
                    else None
                ),
                "years_of_financials": len(inputs["total_assets_by_year"]),
            }
        )

    if summaries:
        print("\n=== FF6 real-data summary (SDAD §2) ===")
        print(pd.DataFrame(summaries).to_string(index=False))
        print(f"\nPer-ticker CSVs written under: {os.path.abspath(args.out)}")
        return 0
    print("No data fetched.")
    return 1


if __name__ == "__main__":
    sys.exit(main())

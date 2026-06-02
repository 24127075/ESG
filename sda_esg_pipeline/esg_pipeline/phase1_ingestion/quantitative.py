"""Quantitative engine (SDAD §2).

Pulls the raw quantitative inputs for the Fama-French 6-factor model [Fama &
French 2015] from ``vnstock`` (Sponsor Tier) across ~310 listed VN tickers.

Resilience (SDAD §2.3): every remote call is wrapped with ``tenacity`` retry
using exponential backoff; if the API keeps refusing we switch over to a
static CSV snapshot so the pipeline never hard-stops.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ..common.exceptions import FallbackUnavailableException

logger = logging.getLogger(__name__)

STATIC_FALLBACK_ROOT = os.getenv("STATIC_FALLBACK_ROOT", "./data/static_fallback")


# ── Data Field Mapping for the FF6 model (SDAD §2.2, table on p.4) ──────────
@dataclass(frozen=True)
class FieldSpec:
    group: str           # data group (accounting / trading / macro)
    system_field: str    # canonical field name used downstream
    api_call: str        # vnstock call that produces it (documentation)
    econometric_goal: str


FF6_FIELD_MAPPING: tuple[FieldSpec, ...] = (
    FieldSpec(
        "accounting_yearly", "operating_profit",
        "Fundamental().equity.income_statement(symbol, period='Y')",
        "Profitability numerator — input to the RMW factor.",
    ),
    FieldSpec(
        "accounting_yearly", "total_assets",
        "Fundamental().equity.balance_sheet(symbol, period='Y')",
        "Asset growth between t and t-1 — input to the CMA factor.",
    ),
    FieldSpec(
        "trading_monthly", "adj_close",
        "Market().equity(symbol).ohlcv()",
        "Realised monthly return R_t — dependent variable.",
    ),
    FieldSpec(
        "trading_monthly", "shares_outstanding",
        "Reference().company(symbol).info()",
        "Number of shares outstanding.",
    ),
    FieldSpec(
        "trading_monthly", "market_equity",
        "Computed in processing layer",
        "ME = Close × Shares_Outstanding. Size bucket for the SMB factor.",
    ),
    FieldSpec(
        "macro_monthly", "interest_rate",
        "Market().interest_rate()",
        "10Y government bond yield — risk-free rate R_f.",
    ),
)


# ── Remote fetch with retry + static-CSV fallback (SDAD §2.3) ───────────────
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
)
def fetch_vnstock_api(ticker: str, year: int):
    """Call the vnstock Sponsor-Tier financial report endpoint.

    Retries up to 3 times with exponential backoff (2s → 10s). Imported
    lazily so the module loads even where vnstock is not installed.
    """
    from vnstock_data import Insights

    return Insights().get_financial_report(ticker, year)


def fetch_financial_data(ticker: str, year: int) -> pd.DataFrame:
    """Fetch one ticker-year, falling back to a static CSV on repeated failure.

    Raises :class:`FallbackUnavailableException` when the API is down *and* no
    local snapshot exists for the requested ticker/year.
    """
    try:
        return fetch_vnstock_api(ticker, year)
    except Exception as exc:  # noqa: BLE001 - any API/transport failure
        fallback_path = os.path.join(
            STATIC_FALLBACK_ROOT, "financials", f"{ticker}_{year}.csv"
        )
        if os.path.exists(fallback_path):
            logger.warning(
                "vnstock API failed (%s). Falling back to %s", exc, fallback_path
            )
            return pd.read_csv(fallback_path)
        raise FallbackUnavailableException(
            f"API failed & no fallback found for {ticker} {year}"
        ) from exc


# ── Derived quantities computed in the processing layer ─────────────────────
def compute_market_equity(close: pd.Series, shares_outstanding: pd.Series) -> pd.Series:
    """ME = Close × Shares_Outstanding (SDAD §2.2, row 5 — SMB size bucket)."""
    return close.astype(float) * shares_outstanding.astype(float)


def compute_asset_growth(total_assets: pd.Series) -> pd.Series:
    """Year-over-year asset growth (t vs t-1) — CMA factor input (row 2)."""
    return total_assets.astype(float).pct_change()

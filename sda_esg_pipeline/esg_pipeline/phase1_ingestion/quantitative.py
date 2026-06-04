"""Quantitative engine (SDAD §2) — wired to the FREE public ``vnstock`` 3.x API.

Pulls the raw quantitative inputs for the Fama-French 6-factor model [Fama &
French 2015] for the listed-VN universe.

IMPORTANT — deviation from the SDAD on purpose (see docs/DANH_GIA_SDA.md, issue
#1): the SDAD §2.3 snippet calls ``vnstock_data.Insights().get_financial_report``,
which only exists in the **paid Sponsor Tier**. This implementation targets the
**free community** ``vnstock`` package instead, whose real surface is::

    Vnstock().stock(symbol, source='VCI').quote.history(...)       # OHLCV
    Vnstock().stock(symbol, source='VCI').finance.income_statement(period='year')
    Vnstock().stock(symbol, source='VCI').finance.balance_sheet(period='year')
    Vnstock().stock(symbol, source='VCI').company.overview()       # shares, mktcap

The §2.2 *field mapping table* (below) documents both the original SDAD call and
the real call now used.

Resilience (SDAD §2.3): every remote call is wrapped with ``tenacity`` retry
using exponential backoff; if the API keeps refusing we switch over to a static
CSV snapshot so the pipeline never hard-stops.
"""
from __future__ import annotations

import contextlib
import io
import logging
import os
from dataclasses import dataclass

import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from ..common.exceptions import FallbackUnavailableException

logger = logging.getLogger(__name__)

STATIC_FALLBACK_ROOT = os.getenv("STATIC_FALLBACK_ROOT", "./data/static_fallback")
# VCI / TCBS / MSN are the community data sources; VCI is the most complete.
VNSTOCK_SOURCE = os.getenv("VNSTOCK_SOURCE", "VCI")

# Line-item labels (English) in the community financial statements.
_OPERATING_PROFIT_ITEM = "operating profit/(loss)"
_TOTAL_ASSETS_ITEM = "total assets"


# ── Data Field Mapping for the FF6 model (SDAD §2.2, table on p.4) ──────────
@dataclass(frozen=True)
class FieldSpec:
    group: str            # data group (accounting / trading / macro)
    system_field: str     # canonical field name used downstream
    sdad_call: str        # the call the SDAD documented (Sponsor Tier)
    real_call: str        # the FREE public call actually used here
    econometric_goal: str


FF6_FIELD_MAPPING: tuple[FieldSpec, ...] = (
    FieldSpec(
        "accounting_yearly", "operating_profit",
        "Fundamental().equity.income_statement(symbol, period='Y')",
        "stock.finance.income_statement(period='year') → 'Operating profit/(loss)'",
        "Profitability numerator — input to the RMW factor.",
    ),
    FieldSpec(
        "accounting_yearly", "total_assets",
        "Fundamental().equity.balance_sheet(symbol, period='Y')",
        "stock.finance.balance_sheet(period='year') → 'Total Assets'",
        "Asset growth between t and t-1 — input to the CMA factor.",
    ),
    FieldSpec(
        "trading_monthly", "adj_close",
        "Market().equity(symbol).ohlcv()",
        "stock.quote.history(start, end, interval='1M') → 'close'",
        "Realised monthly return R_t — dependent variable.",
    ),
    FieldSpec(
        "trading_monthly", "shares_outstanding",
        "Reference().company(symbol).info()",
        "stock.company.overview() → 'issue_share'",
        "Number of shares outstanding.",
    ),
    FieldSpec(
        "trading_monthly", "market_equity",
        "Computed in processing layer",
        "compute_market_equity(close, shares_outstanding)",
        "ME = Close × Shares_Outstanding. Size bucket for the SMB factor.",
    ),
    FieldSpec(
        "macro_monthly", "interest_rate",
        "Market().interest_rate()",
        "External source (HNX bond board / SBV); not in free vnstock — see §2.3 note",
        "10Y government bond yield — risk-free rate R_f.",
    ),
)


# ── vnstock client plumbing ─────────────────────────────────────────────────
@contextlib.contextmanager
def _quiet_stdout():
    """Silence the vnstock community banner/ads printed on client construction."""
    with contextlib.redirect_stdout(io.StringIO()):
        yield


def _stock(symbol: str, source: str | None = None):
    """Build a vnstock ``stock`` handle (lazy import; banner suppressed)."""
    from vnstock import Vnstock

    with _quiet_stdout():
        return Vnstock().stock(symbol=symbol, source=source or VNSTOCK_SOURCE)


_RETRY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)


# ── Raw real-data fetchers (retry + exponential backoff, SDAD §2.3) ─────────
@_RETRY
def fetch_ohlcv(symbol: str, start: str, end: str, interval: str = "1M") -> pd.DataFrame:
    """Monthly (default) OHLCV history — the basis for the realised return R_t."""
    with _quiet_stdout():
        return _stock(symbol).quote.history(start=start, end=end, interval=interval)


@_RETRY
def fetch_income_statement(symbol: str, period: str = "year") -> pd.DataFrame:
    """Yearly income statement (long format: one row per line item, years as cols)."""
    with _quiet_stdout():
        return _stock(symbol).finance.income_statement(period=period, lang="en")


@_RETRY
def fetch_balance_sheet(symbol: str, period: str = "year") -> pd.DataFrame:
    """Yearly balance sheet (long format)."""
    with _quiet_stdout():
        return _stock(symbol).finance.balance_sheet(period=period, lang="en")


@_RETRY
def fetch_company_overview(symbol: str) -> pd.DataFrame:
    """Company overview — carries ``issue_share`` and ``market_cap``."""
    with _quiet_stdout():
        return _stock(symbol).company.overview()


# ── Long-format helpers ──────────────────────────────────────────────────────
def line_item_by_year(statement: pd.DataFrame, item_en: str) -> dict[int, float]:
    """Pull one statement line item as ``{year: value}``.

    The community statements are transposed: an ``item_en`` column names each
    line and the remaining numeric-string columns are fiscal years.
    """
    if "item_en" not in statement.columns:
        return {}
    mask = statement["item_en"].astype(str).str.strip().str.lower() == item_en.lower()
    rows = statement[mask]
    if rows.empty:
        return {}
    row = rows.iloc[0]
    out: dict[int, float] = {}
    for col in statement.columns:
        if str(col).isdigit():
            try:
                out[int(col)] = float(row[col])
            except (TypeError, ValueError):
                continue
    return out


def shares_outstanding(overview: pd.DataFrame) -> float | None:
    """Read ``issue_share`` from a company-overview frame, if present."""
    if "issue_share" in overview.columns and not overview.empty:
        try:
            return float(overview.iloc[0]["issue_share"])
        except (TypeError, ValueError):
            return None
    return None


# ── FF6 assembly (real data) ─────────────────────────────────────────────────
def fetch_ff6_inputs(symbol: str, start: str, end: str) -> dict:
    """Assemble the real FF6 inputs for one symbol.

    Returns a dict with monthly OHLCV, yearly operating_profit & total_assets
    (+ asset growth), shares outstanding and derived market equity. This is the
    concrete "real data" artefact the SDAD §2 describes.
    """
    income = fetch_income_statement(symbol)
    balance = fetch_balance_sheet(symbol)
    overview = fetch_company_overview(symbol)
    ohlcv = fetch_ohlcv(symbol, start, end)

    op_by_year = line_item_by_year(income, _OPERATING_PROFIT_ITEM)
    ta_by_year = line_item_by_year(balance, _TOTAL_ASSETS_ITEM)
    shares = shares_outstanding(overview)

    ta_series = pd.Series(dict(sorted(ta_by_year.items())), dtype="float64")
    asset_growth = ta_series.pct_change() if not ta_series.empty else pd.Series(dtype="float64")

    market_equity = None
    if shares is not None and not ohlcv.empty and "close" in ohlcv.columns:
        market_equity = compute_market_equity(ohlcv["close"], pd.Series(shares, index=ohlcv.index))

    return {
        "symbol": symbol,
        "operating_profit_by_year": op_by_year,
        "total_assets_by_year": ta_by_year,
        "asset_growth_by_year": asset_growth.to_dict(),
        "shares_outstanding": shares,
        "ohlcv": ohlcv,
        "market_equity": market_equity,
    }


# ── Backward-compatible report fetch with static-CSV fallback (SDAD §2.3) ────
def _build_financial_report(symbol: str, year: int) -> pd.DataFrame:
    """One tidy report (metric, value) for a ticker-year from the real API."""
    income = fetch_income_statement(symbol)
    balance = fetch_balance_sheet(symbol)
    op = line_item_by_year(income, _OPERATING_PROFIT_ITEM).get(year)
    ta = line_item_by_year(balance, _TOTAL_ASSETS_ITEM).get(year)
    return pd.DataFrame(
        [
            {"ticker": symbol, "year": year, "metric": "operating_profit", "value": op},
            {"ticker": symbol, "year": year, "metric": "total_assets", "value": ta},
        ]
    )


def fetch_financial_data(ticker: str, year: int) -> pd.DataFrame:
    """Fetch one ticker-year, falling back to a static CSV on repeated failure.

    Raises :class:`FallbackUnavailableException` when the API is down *and* no
    local snapshot exists for the requested ticker/year.
    """
    try:
        return _build_financial_report(ticker, year)
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

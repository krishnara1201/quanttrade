"""Portfolio-level backtests: run one strategy independently across a
basket of tickers, each funded from a custom fixed weight of the initial
capital, and aggregate the results into one portfolio equity curve/metrics.

Each ticker's sub-account runs through the existing, unmodified
StrategyExecutor.backtest() — this module only validates the basket,
allocates capital, and aggregates the per-ticker results. There is no
rebalancing and no cross-ticker strategy logic (see the design spec at
docs/superpowers/specs/2026-08-13-portfolio-backtests-design.md).
"""
from datetime import datetime
from typing import Any, Dict, List

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MarketData


def normalize_weights(tickers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate a portfolio ticker list and normalize weights to sum to 1.0.

    Args:
        tickers: [{"ticker": str, "weight": float}, ...]. Weights need not
            already sum to 1.0 or 100 — e.g. {2, 1} normalizes to {2/3, 1/3}.

    Raises:
        ValueError: fewer than 2 tickers, a non-positive weight, or a
            duplicate ticker.
    """
    if len(tickers) < 2:
        raise ValueError("Portfolio backtest requires at least 2 tickers with positive weights")

    seen = set()
    for t in tickers:
        if t["weight"] <= 0:
            raise ValueError(f"Weight for {t['ticker']} must be positive")
        if t["ticker"] in seen:
            raise ValueError(f"Duplicate ticker in portfolio: {t['ticker']}")
        seen.add(t["ticker"])

    total = sum(t["weight"] for t in tickers)
    return [{"ticker": t["ticker"], "weight": t["weight"] / total} for t in tickers]


async def _check_ticker_coverage(ticker: str, start_dt: datetime, end_dt: datetime, db: AsyncSession) -> None:
    """Raise ValueError unless `ticker` has data spanning the full
    [start_dt, end_dt] range — mirrors the range check GET /api/data/{ticker}/range
    already exposes, so a portfolio run fails fast with a clear message
    instead of silently aggregating over partial data."""
    result = await db.execute(
        select(
            func.min(MarketData.date), func.max(MarketData.date), func.count(MarketData.id)
        ).where(MarketData.ticker == ticker)
    )
    min_date, max_date, count = result.one()
    if not count:
        raise ValueError(f"No market data found for ticker '{ticker}'")
    if min_date > start_dt or max_date < end_dt:
        raise ValueError(
            f"{ticker} has data from {min_date.date()} to {max_date.date()}, "
            f"which does not cover the requested {start_dt.date()} to {end_dt.date()}"
        )


def aggregate_equity_curves(
    per_ticker_curves: Dict[str, List[Dict[str, Any]]],
    allocated_capital: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Combine per-ticker equity curves (each a chronologically-ordered list
    of {'date', 'equity'} dicts) into one portfolio equity curve summed
    across tickers. Dates are the union across all tickers; a ticker with no
    entry for a given date uses its last-known equity (forward-fill), or its
    starting allocated_capital for any date before its first data point."""
    all_dates = sorted({point["date"] for curve in per_ticker_curves.values() for point in curve})
    lookups = {
        ticker: {p["date"]: p["equity"] for p in curve}
        for ticker, curve in per_ticker_curves.items()
    }

    last_known = dict(allocated_capital)
    portfolio_curve = []
    for date in all_dates:
        total = 0.0
        for ticker, lookup in lookups.items():
            if date in lookup:
                last_known[ticker] = lookup[date]
            total += last_known[ticker]
        portfolio_curve.append({"date": date, "equity": total})
    return portfolio_curve

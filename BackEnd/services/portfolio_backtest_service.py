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

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import MarketData, PortfolioBacktestResult, Strategy, User
from services.strategy_executor import StrategyExecutor, max_drawdown_pct, sharpe_ratio


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


def aggregate_portfolio_metrics(
    per_ticker_results: Dict[str, Dict[str, Any]],
    portfolio_equity_curve: List[Dict[str, Any]],
    initial_capital: float,
) -> Dict[str, Any]:
    """Pool trades and equity across all tickers in the basket into one set
    of portfolio-level metrics, using the same field names as a single-ticker
    backtest's metrics so the frontend can render either with one component."""
    all_trades = [
        trade for result in per_ticker_results.values() for trade in result["trades"]
    ]

    final_capital = portfolio_equity_curve[-1]["equity"] if portfolio_equity_curve else initial_capital
    total_return = final_capital - initial_capital
    return_pct = (total_return / initial_capital) * 100 if initial_capital else 0.0

    exits = [t for t in all_trades if t["type"] == "exit"]
    wins = len([t for t in exits if t.get("pnl", 0) > 0])
    win_rate = (wins / len(exits) * 100) if exits else 0.0

    return {
        "total_return": float(total_return),
        "return_pct": float(return_pct),
        "final_capital": float(final_capital),
        "win_rate": float(win_rate),
        "num_trades": len(exits),
        "max_drawdown_pct": max_drawdown_pct(portfolio_equity_curve),
        "sharpe_ratio": sharpe_ratio(portfolio_equity_curve),
    }


async def run_portfolio_backtest(
    strategy_id: int,
    tickers: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    db: AsyncSession,
    user: User,
) -> Dict[str, Any]:
    """Run a strategy independently across a basket of tickers, each funded
    from a fixed weight of initial_capital, and return the aggregated
    portfolio result. Raises ValueError for any validation/ownership/
    execution failure — callers (e.g. the router) translate that to an
    HTTP 400/403."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise ValueError("Strategy not found")
    if strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise ValueError("start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    allocations = normalize_weights(tickers)

    for alloc in allocations:
        await _check_ticker_coverage(alloc["ticker"], start_dt, end_dt, db)

    per_ticker_results: Dict[str, Any] = {}
    allocated_capital: Dict[str, float] = {}

    for alloc in allocations:
        ticker = alloc["ticker"]
        sub_capital = initial_capital * alloc["weight"]
        allocated_capital[ticker] = sub_capital

        data_result = await db.execute(
            select(MarketData).where(
                MarketData.ticker == ticker,
                MarketData.date >= start_dt,
                MarketData.date <= end_dt,
            ).order_by(MarketData.date)
        )
        rows = data_result.scalars().all()
        if not rows:
            raise ValueError(f"No market data found for {ticker} between {start_dt.date()} and {end_dt.date()}")
        df = pd.DataFrame([
            {
                "date": r.date, "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
            }
            for r in rows
        ])
        df.set_index("date", inplace=True)

        try:
            executor = StrategyExecutor(strategy.parameters, code=strategy.code)
            result = executor.backtest(
                df, initial_capital=sub_capital,
                commission_pct=commission_pct, slippage_pct=slippage_pct,
            )
        except Exception as e:
            raise ValueError(f"Backtest execution failed for {ticker}: {str(e)}")

        per_ticker_results[ticker] = {**result, "allocated_capital": sub_capital}

    portfolio_equity_curve = aggregate_equity_curves(
        {t: r["equity_curve"] for t, r in per_ticker_results.items()},
        allocated_capital,
    )
    metrics = aggregate_portfolio_metrics(per_ticker_results, portfolio_equity_curve, initial_capital)

    record = PortfolioBacktestResult(
        strategy_id=strategy_id,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allocations=allocations,
        results=metrics,
        equity_curve=portfolio_equity_curve,
        per_ticker=per_ticker_results,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "id": record.id,
        "strategy_id": strategy_id,
        "allocations": allocations,
        "metrics": metrics,
        "equity_curve": portfolio_equity_curve,
        "per_ticker": per_ticker_results,
        "created_at": record.created_at.isoformat(),
    }


async def get_portfolio_backtest_results(strategy_id: int, db: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """Summary rows (no per_ticker payload) for all portfolio backtests of a strategy."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if not strategy or strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    result = await db.execute(
        select(PortfolioBacktestResult)
        .where(PortfolioBacktestResult.strategy_id == strategy_id)
        .order_by(PortfolioBacktestResult.created_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "allocations": r.allocations,
            "metrics": r.results,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


async def get_portfolio_backtest_detail(portfolio_backtest_id: int, db: AsyncSession, user: User) -> Dict[str, Any]:
    """Full detail for one portfolio backtest, including the per_ticker breakdown."""
    result = await db.execute(
        select(PortfolioBacktestResult)
        .options(selectinload(PortfolioBacktestResult.strategy).selectinload(Strategy.project))
        .where(PortfolioBacktestResult.id == portfolio_backtest_id)
    )
    record = result.scalars().first()
    if not record:
        raise ValueError("Portfolio backtest not found")
    if record.strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "allocations": record.allocations,
        "metrics": record.results,
        "equity_curve": record.equity_curve,
        "per_ticker": record.per_ticker,
        "created_at": record.created_at.isoformat(),
    }

from typing import Optional
from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db
from database.models import User, Strategy, MarketData, BacktestResult
from services.auth_service import get_current_user
from services.strategy_executor import StrategyExecutor, benchmark_equity_curve
import pandas as pd
from datetime import datetime

async def create_pending_backtest(strategy_id: int, ticker: str, start_date: str, end_date: str,
                       initial_capital: float = 10000.0,
                       commission_pct: float = 0.1,
                       slippage_pct: float = 0.05,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user),
                       *,
                       allow_short: bool = False,
                       stop_loss_pct: Optional[float] = None,
                       take_profit_pct: Optional[float] = None) -> BacktestResult:
    """Validate ownership/input and insert a pending BacktestResult row.
    The actual computation happens later in execute_backtest, run
    out-of-process by a Celery worker (see tasks.py)."""

    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    if stop_loss_pct is not None and stop_loss_pct <= 0:
        raise HTTPException(status_code=400, detail="stop_loss_pct must be positive")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise HTTPException(status_code=400, detail="take_profit_pct must be positive")

    result_record = BacktestResult(
        strategy_id=strategy_id,
        ticker=ticker,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allow_short=allow_short,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        status="pending",
    )
    db.add(result_record)
    await db.commit()
    await db.refresh(result_record)
    return result_record


async def execute_backtest(backtest_result_id: int, db: AsyncSession) -> None:
    """Run the backtest computation for an already-created pending
    BacktestResult row and write the outcome back onto that same row.
    Runs inside a Celery worker via asyncio.run() (see tasks.py). Never
    raises — any failure (no market data, bad strategy code, etc.) is
    recorded on the row as status='failed' + error_message, since a worker
    has no HTTP response to raise into."""
    result = await db.execute(
        select(BacktestResult).where(BacktestResult.id == backtest_result_id)
    )
    record = result.scalars().first()
    if record is None:
        return

    record.status = "running"
    await db.commit()

    try:
        data_result = await db.execute(
            select(MarketData).where(
                MarketData.ticker == record.ticker,
                MarketData.date >= record.start_date,
                MarketData.date <= record.end_date,
            ).order_by(MarketData.date)
        )
        market_data_rows = data_result.scalars().all()
        if not market_data_rows:
            raise ValueError("No market data found in the specified date range")

        data_dicts = [
            {
                'date': row.date,
                'open': float(row.open),
                'high': float(row.high),
                'low': float(row.low),
                'close': float(row.close),
                'volume': float(row.volume),
            }
            for row in market_data_rows
        ]
        df = pd.DataFrame(data_dicts)
        df.set_index('date', inplace=True)

        strategy_result = await db.execute(
            select(Strategy).where(Strategy.id == record.strategy_id)
        )
        strategy = strategy_result.scalars().first()
        if strategy is None:
            raise ValueError("Strategy not found")

        executor = StrategyExecutor(strategy.parameters, code=strategy.code)
        backtest_results = executor.backtest(
            df, initial_capital=record.initial_capital,
            commission_pct=record.commission_pct, slippage_pct=record.slippage_pct,
            allow_short=record.allow_short, stop_loss_pct=record.stop_loss_pct,
            take_profit_pct=record.take_profit_pct,
        )
        benchmark_curve = benchmark_equity_curve(df, record.initial_capital)
    except Exception as e:
        await db.rollback()
        record.status = "failed"
        record.error_message = f"{type(e).__name__}: {e}"
        await db.commit()
        return

    record.results = backtest_results['metrics']
    record.trades = backtest_results['trades']
    record.signals = backtest_results['signals']
    record.equity_curve = backtest_results['equity_curve']
    record.benchmark_equity_curve = benchmark_curve
    record.status = "success"
    await db.commit()


async def get_backtest_results(strategy_id: int, db: AsyncSession = Depends(get_db),
                               user: User = Depends(get_current_user)):
    """Retrieve backtest results for a strategy"""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if not strategy or strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    result = await db.execute(
        select(BacktestResult).where(BacktestResult.strategy_id == strategy_id).order_by(BacktestResult.created_at.desc())
    )
    backtest_results = result.scalars().all()

    return [
        {
            'id': r.id,
            'strategy_id': r.strategy_id,
            'status': r.status,
            'metrics': r.results,
            'num_trades': len(r.trades or []),
            'created_at': r.created_at.isoformat(),
        }
        for r in backtest_results
    ]

async def get_backtest_graph(strategy_id: int, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    result = await db.execute(
        select(BacktestResult).where(BacktestResult.strategy_id == strategy_id)
    )
    backtest_results = result.scalars().all()
    if not backtest_results:
        raise HTTPException(status_code=404, detail="No backtest results found for the specified strategy")

    graph_url = "http://example.com/dummy_graph.png"
    return {"graph_url": graph_url}

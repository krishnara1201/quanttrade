from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db
from database.models import User, Strategy, MarketData, BacktestResult
from services.auth_service import get_current_user
from services.strategy_executor import StrategyExecutor
import pandas as pd
from datetime import datetime

async def run_backtest(strategy_id: int, ticker: str, start_date: str, end_date: str,
                       initial_capital: float = 10000.0,
                       commission_pct: float = 0.1,
                       slippage_pct: float = 0.05,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Run backtest for a strategy on market data"""
    
    # Fetch strategy
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    # Verify ownership
    if strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    # start_date/end_date arrive as plain strings (e.g. "2023-01-01") from the
    # request body. SQLite tolerates comparing a DateTime column to a raw str,
    # but Postgres/asyncpg does not ("operator does not exist: timestamp
    # without time zone >= character varying") — parse to datetime up front
    # so the comparison is well-typed on every backend.
    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    # Fetch market data
    data_result = await db.execute(
        select(MarketData).where(
            MarketData.ticker == ticker,
            MarketData.date >= start_dt,
            MarketData.date <= end_dt
        ).order_by(MarketData.date)
    )
    market_data_rows = data_result.scalars().all()
    if not market_data_rows:
        raise HTTPException(status_code=404, detail="No market data found in the specified date range")
    
    # Convert to DataFrame
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
    
    # Execute strategy
    try:
        executor = StrategyExecutor(strategy.parameters)
        backtest_results = executor.backtest(
            df, initial_capital=initial_capital,
            commission_pct=commission_pct, slippage_pct=slippage_pct
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Backtest execution failed: {str(e)}")

    # Save results
    result_record = BacktestResult(
        strategy_id=strategy_id,
        start_date=start_dt,
        end_date=end_dt,
        results=backtest_results['metrics'],
        trades=backtest_results['trades'],
        signals=backtest_results['signals'],
        equity_curve=backtest_results['equity_curve'],
    )

    db.add(result_record)
    await db.commit()
    await db.refresh(result_record)

    return {
        'id': result_record.id,
        'strategy_id': strategy_id,
        'metrics': backtest_results['metrics'],
        'trades': backtest_results['trades'],
        'signals': backtest_results['signals'],
        'equity_curve': backtest_results['equity_curve'],
        'created_at': result_record.created_at.isoformat(),
    }

async def get_backtest_results(strategy_id: int, db: AsyncSession = Depends(get_db),
                               user: User = Depends(get_current_user)):
    """Retrieve backtest results for a strategy"""
    # Verify ownership
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
            'metrics': r.results,
            'num_trades': len(r.trades),
            'created_at': r.created_at.isoformat(),
        }
        for r in backtest_results
    ]
    return backtest_results

async def get_backtest_graph(strategy_id: int, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    # Implementation for generating backtest graph
    result = await db.execute(
        select(BacktestResult).where(BacktestResult.strategy_id == strategy_id)
    )
    backtest_results = result.scalars().all()
    if not backtest_results:
        raise HTTPException(status_code=404, detail="No backtest results found for the specified strategy")
    
    # Here you would implement the actual graph generation logic using the backtest results
    # For demonstration purposes, we'll just return a dummy graph URL
    graph_url = "http://example.com/dummy_graph.png"
    
    return {"graph_url": graph_url}
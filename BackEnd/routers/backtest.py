from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Strategy, BacktestResult
from database.connection import get_db
from services.auth_service import get_current_user
from services.backtest_service import run_backtest, get_backtest_results
from datetime import datetime

router = APIRouter(prefix="/api/backtest", tags=["backtest"])

class BacktestRequest(BaseModel):
    strategy_id: int
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05

@router.post("/run")
async def run_backtest_endpoint(
    req: BacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Run a backtest for a strategy"""
    return await run_backtest(
        req.strategy_id,
        req.ticker,
        req.start_date,
        req.end_date,
        req.initial_capital,
        req.commission_pct,
        req.slippage_pct,
        db,
        user
    )

@router.get("/results/{strategy_id}")
async def get_backtest_results_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get all backtest results for a strategy"""
    return await get_backtest_results(strategy_id, db, user)

@router.get("/{backtest_id}")
async def get_backtest_detail(
    backtest_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed backtest results with signals"""
    result = await db.execute(
        select(BacktestResult).where(BacktestResult.id == backtest_id)
    )
    backtest = result.scalars().first()
    
    if not backtest:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Backtest not found")
    
    # Verify ownership
    strategy = backtest.strategy
    if strategy.project.owner_id != user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Unauthorized")
    
    return {
        'id': backtest.id,
        'strategy_id': backtest.strategy_id,
        'metrics': backtest.results,
        'trades': backtest.trades,
        'signals': backtest.signals,
        'equity_curve': backtest.equity_curve,
        'created_at': backtest.created_at.isoformat(),
    }

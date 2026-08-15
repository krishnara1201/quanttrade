from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Strategy, BacktestResult
from database.connection import get_db
from services.auth_service import get_current_user
from services.backtest_service import create_pending_backtest, get_backtest_results
from services.portfolio_backtest_service import (
    create_pending_portfolio_backtest, get_portfolio_backtest_results, get_portfolio_backtest_detail,
)
from services.walk_forward_service import (
    create_pending_walk_forward_backtest, get_walk_forward_backtest_results,
    get_walk_forward_backtest_detail, estimate_fold_count,
)
from services.sandbox_executor import DEFAULT_TIMEOUT_S
from tasks import run_backtest_task, run_portfolio_backtest_task, walk_forward_task
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
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class PortfolioTickerWeight(BaseModel):
    ticker: str
    weight: float

class PortfolioBacktestRequest(BaseModel):
    strategy_id: int
    tickers: List[PortfolioTickerWeight]
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

class WalkForwardBacktestRequest(BaseModel):
    strategy_id: int
    ticker: str
    start_date: str
    end_date: str
    test_window_days: int
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None

@router.post("/run")
async def run_backtest_endpoint(
    req: BacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Create a pending backtest and enqueue it for async execution."""
    record = await create_pending_backtest(
        req.strategy_id, req.ticker, req.start_date, req.end_date,
        req.initial_capital, req.commission_pct, req.slippage_pct,
        db, user,
        allow_short=req.allow_short, stop_loss_pct=req.stop_loss_pct, take_profit_pct=req.take_profit_pct,
    )
    try:
        run_backtest_task.delay(record.id)
    except Exception as e:
        record.status = "failed"
        record.error_message = f"Could not enqueue task: {e}"
        await db.commit()
        raise HTTPException(status_code=503, detail="Task queue unavailable, please try again")
    return {
        'id': record.id,
        'strategy_id': record.strategy_id,
        'status': record.status,
        'created_at': record.created_at.isoformat(),
    }

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
        select(BacktestResult)
        .options(selectinload(BacktestResult.strategy).selectinload(Strategy.project))
        .where(BacktestResult.id == backtest_id)
    )
    backtest = result.scalars().first()

    if not backtest:
        raise HTTPException(status_code=404, detail="Backtest not found")

    strategy = backtest.strategy
    if strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    return {
        'id': backtest.id,
        'strategy_id': backtest.strategy_id,
        'status': backtest.status,
        'error_message': backtest.error_message,
        'metrics': backtest.results,
        'trades': backtest.trades,
        'signals': backtest.signals,
        'equity_curve': backtest.equity_curve,
        'benchmark_equity_curve': backtest.benchmark_equity_curve,
        'created_at': backtest.created_at.isoformat(),
    }

@router.post("/run-portfolio")
async def run_portfolio_backtest_endpoint(
    req: PortfolioBacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Create a pending portfolio backtest and enqueue it for async execution."""
    try:
        record = await create_pending_portfolio_backtest(
            req.strategy_id,
            [t.model_dump() for t in req.tickers],
            req.start_date,
            req.end_date,
            req.initial_capital,
            req.commission_pct,
            req.slippage_pct,
            db,
            user,
            allow_short=req.allow_short,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
        )
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

    try:
        run_portfolio_backtest_task.delay(record.id)
    except Exception as e:
        record.status = "failed"
        record.error_message = f"Could not enqueue task: {e}"
        await db.commit()
        raise HTTPException(status_code=503, detail="Task queue unavailable, please try again")
    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "allocations": record.allocations,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }

@router.get("/portfolio/results/{strategy_id}")
async def get_portfolio_backtest_results_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get all portfolio backtest results for a strategy"""
    try:
        return await get_portfolio_backtest_results(strategy_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

@router.get("/portfolio/{portfolio_backtest_id}")
async def get_portfolio_backtest_detail_endpoint(
    portfolio_backtest_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed portfolio backtest results, including per-ticker breakdown"""
    try:
        return await get_portfolio_backtest_detail(portfolio_backtest_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

@router.post("/run-walk-forward")
async def run_walk_forward_backtest_endpoint(
    req: WalkForwardBacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Create a pending walk-forward backtest and enqueue it for async
    execution. Fold computation (and therefore date-range validation) happens
    inside the task, not here — see execute_walk_forward."""
    try:
        record = await create_pending_walk_forward_backtest(
            req.strategy_id, req.ticker, req.start_date, req.end_date, req.test_window_days,
            req.initial_capital, req.commission_pct, req.slippage_pct, db, user,
            allow_short=req.allow_short, stop_loss_pct=req.stop_loss_pct, take_profit_pct=req.take_profit_pct,
        )
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

    start_dt = datetime.fromisoformat(req.start_date)
    end_dt = datetime.fromisoformat(req.end_date)
    estimated_folds = estimate_fold_count(start_dt, end_dt, req.test_window_days)
    # Each fold is one sandboxed subprocess call (budgeted DEFAULT_TIMEOUT_S)
    # plus a small margin; +30s overall slack for the DB round trips between
    # folds. Deliberately generous since estimated_folds already
    # over-estimates (see estimate_fold_count's docstring).
    time_limit = estimated_folds * (DEFAULT_TIMEOUT_S + 5) + 30
    try:
        walk_forward_task.apply_async(
            args=[record.id], time_limit=time_limit, soft_time_limit=max(time_limit - 30, 1),
        )
    except Exception as e:
        record.status = "failed"
        record.error_message = f"Could not enqueue task: {e}"
        await db.commit()
        raise HTTPException(status_code=503, detail="Task queue unavailable, please try again")
    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "ticker": record.ticker,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }

@router.get("/walk-forward/results/{strategy_id}")
async def get_walk_forward_backtest_results_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get all walk-forward backtest results for a strategy"""
    try:
        return await get_walk_forward_backtest_results(strategy_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

@router.get("/walk-forward/{walk_forward_backtest_id}")
async def get_walk_forward_backtest_detail_endpoint(
    walk_forward_backtest_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed walk-forward backtest results, including per-fold breakdown"""
    try:
        return await get_walk_forward_backtest_detail(walk_forward_backtest_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

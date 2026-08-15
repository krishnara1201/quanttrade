"""Walk-forward (expanding-window out-of-sample) evaluation for custom-code
(ML) strategies. Instead of fitting/predicting once over an entire date
range (which lets a model implicitly "see" the whole history before being
scored on any of it), this splits the range into expanding train/test
folds, re-runs StrategyExecutor.generate_signals() fresh each fold, and
stitches the out-of-sample results into one continuous equity curve with
capital compounding across folds.

Scoped to custom_code strategies only — see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md.
"""
import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Strategy, User, WalkForwardBacktestResult


def compute_fold_boundaries(
    start_dt: datetime, end_dt: datetime, test_window_days: int
) -> List[Dict[str, Any]]:
    """Expanding-window fold boundaries. The first fold's train window is
    max(365 days, 25% of the total range), anchored at start_dt; each
    fold's test window is test_window_days long (inclusive), stepping
    forward from the end of the previous fold's test window. A trailing
    remainder shorter than test_window_days is dropped rather than becoming
    a short partial fold.

    Returns a list of {fold_index, train_start, train_end, test_start,
    test_end} dicts (train_end/test_end inclusive).

    Raises:
        ValueError: the range doesn't fit even one full fold.
    """
    total_days = (end_dt - start_dt).days
    initial_train_days = max(365, int(total_days * 0.25))

    folds = []
    fold_index = 0
    test_start = start_dt + timedelta(days=initial_train_days)
    while True:
        test_end = test_start + timedelta(days=test_window_days - 1)
        if test_end > end_dt:
            break
        folds.append({
            "fold_index": fold_index,
            "train_start": start_dt,
            "train_end": test_start - timedelta(days=1),
            "test_start": test_start,
            "test_end": test_end,
        })
        fold_index += 1
        test_start = test_end + timedelta(days=1)

    if not folds:
        raise ValueError("date range too short for the requested test window")
    return folds


def estimate_fold_count(start_dt: datetime, end_dt: datetime, test_window_days: int) -> int:
    """Conservative (over-)estimate of fold count, used to size the Celery
    task's time limit before folds are actually computed (that requires the
    market data itself, which isn't loaded until the task runs). Deliberately
    ignores the initial-train-window subtraction that compute_fold_boundaries
    applies, so this always estimates at or above the real fold count —
    erring toward a longer, safer time limit rather than a tight one that
    could clip a real run."""
    total_days = (end_dt - start_dt).days
    return max(1, -(-total_days // test_window_days))  # ceiling division


async def create_pending_walk_forward_backtest(
    strategy_id: int,
    ticker: str,
    start_date: str,
    end_date: str,
    test_window_days: int,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    db: AsyncSession,
    user: User,
    *,
    allow_short: bool = False,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
) -> WalkForwardBacktestResult:
    """Validate ownership/mode/input and insert a pending
    WalkForwardBacktestResult row. The actual fold-by-fold execution
    happens later in execute_walk_forward, run out-of-process by a Celery
    worker. Raises ValueError for any validation/ownership failure —
    callers (the router) translate that to an HTTP 400/403/404."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise ValueError("Strategy not found")
    if strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    config = json.loads(strategy.parameters) if isinstance(strategy.parameters, str) else strategy.parameters
    if config.get("mode") != "custom_code":
        raise ValueError("Walk-forward evaluation requires a custom-code strategy")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise ValueError("start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    if test_window_days <= 0:
        raise ValueError("test_window_days must be positive")
    if stop_loss_pct is not None and stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be positive")

    record = WalkForwardBacktestResult(
        strategy_id=strategy_id,
        ticker=ticker,
        start_date=start_dt,
        end_date=end_dt,
        test_window_days=test_window_days,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allow_short=allow_short,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_walk_forward_backtest_results(strategy_id: int, db: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """Summary rows (no folds/trades/equity_curve payload) for all
    walk-forward backtests of a strategy."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if not strategy or strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    result = await db.execute(
        select(WalkForwardBacktestResult)
        .where(WalkForwardBacktestResult.strategy_id == strategy_id)
        .order_by(WalkForwardBacktestResult.created_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "ticker": r.ticker,
            "test_window_days": r.test_window_days,
            "status": r.status,
            "folds_completed": r.folds_completed,
            "total_folds": r.total_folds,
            "metrics": r.results,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


async def get_walk_forward_backtest_detail(walk_forward_backtest_id: int, db: AsyncSession, user: User) -> Dict[str, Any]:
    """Full detail for one walk-forward backtest, including the per-fold
    breakdown, stitched trades/equity curve, and benchmark curve."""
    result = await db.execute(
        select(WalkForwardBacktestResult)
        .options(selectinload(WalkForwardBacktestResult.strategy).selectinload(Strategy.project))
        .where(WalkForwardBacktestResult.id == walk_forward_backtest_id)
    )
    record = result.scalars().first()
    if not record:
        raise ValueError("Walk-forward backtest not found")
    if record.strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "ticker": record.ticker,
        "test_window_days": record.test_window_days,
        "status": record.status,
        "error_message": record.error_message,
        "folds_completed": record.folds_completed,
        "total_folds": record.total_folds,
        "folds": record.folds,
        "metrics": record.results,
        "trades": record.trades,
        "equity_curve": record.equity_curve,
        "benchmark_equity_curve": record.benchmark_equity_curve,
        "created_at": record.created_at.isoformat(),
    }

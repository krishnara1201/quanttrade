"""Tests for walk-forward (expanding-window out-of-sample) backtest
evaluation: fold boundary computation, per-fold execution with capital
compounding, and the buy-and-hold benchmark overlay. No mocks — real
in-memory sqlite+aiosqlite, real sandboxed strategy execution, matching
this repo's existing testing style (see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md).
"""
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import (
    Base, User, Project, Strategy, WalkForwardBacktestResult, MarketData,
)


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import worker_db
    monkeypatch.setattr(worker_db, "_session_factory", factory)

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_walk_forward_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="strat", project_id=project.id,
            parameters=json.dumps({"name": "strat", "mode": "custom_code"}),
            code="def generate_signals(df):\n    return df['close'] * 0\n",
        )
        db.add(strategy)
        await db.flush()

        wfr = WalkForwardBacktestResult(
            strategy_id=strategy.id,
            ticker="AAPL",
            start_date=datetime(2015, 1, 1),
            end_date=datetime(2020, 1, 1),
            test_window_days=180,
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            total_folds=5,
            folds_completed=2,
            folds=[{"fold_index": 0, "return_pct": 1.5}],
            trades=[{"type": "entry", "price": 100.0}],
            equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0, "fold_index": 0}],
            benchmark_equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0}],
            results={"return_pct": 5.0},
        )
        db.add(wfr)
        await db.commit()
        await db.refresh(wfr)

        result = await db.execute(
            select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == wfr.id)
        )
        loaded = result.scalars().first()
        assert loaded.ticker == "AAPL"
        assert loaded.test_window_days == 180
        assert loaded.total_folds == 5
        assert loaded.folds_completed == 2
        assert loaded.folds == [{"fold_index": 0, "return_pct": 1.5}]
        assert loaded.benchmark_equity_curve == [{"date": "2016-01-01T00:00:00", "equity": 10000.0}]
        assert loaded.status == "pending"  # default


from services.walk_forward_service import compute_fold_boundaries, estimate_fold_count


def test_compute_fold_boundaries_raises_when_range_too_short():
    with pytest.raises(ValueError, match="too short"):
        compute_fold_boundaries(datetime(2024, 1, 1), datetime(2024, 6, 1), test_window_days=90)


def test_compute_fold_boundaries_produces_contiguous_expanding_folds():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    folds = compute_fold_boundaries(start, end, test_window_days=180)

    assert len(folds) >= 5
    for i, fold in enumerate(folds):
        assert fold["fold_index"] == i
        assert fold["train_start"] == start
        assert fold["train_end"] == fold["test_start"] - timedelta(days=1)
        assert (fold["test_end"] - fold["test_start"]).days == 179  # 180-day inclusive window
        assert fold["test_end"] <= end
        if i > 0:
            assert fold["test_start"] == folds[i - 1]["test_end"] + timedelta(days=1)
            assert fold["train_end"] > folds[i - 1]["train_end"]  # expanding


def test_compute_fold_boundaries_drops_short_trailing_remainder():
    start = datetime(2015, 1, 1)
    # 365 (min train) + 180 (one full fold) + a 50-day remainder, too short
    # for a second 180-day fold -> exactly 1 fold, remainder dropped.
    end = start + timedelta(days=365) + timedelta(days=179) + timedelta(days=50)
    folds = compute_fold_boundaries(start, end, test_window_days=180)
    assert len(folds) == 1
    assert folds[0]["test_end"] < end


def test_estimate_fold_count_is_a_conservative_upper_bound():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    actual = len(compute_fold_boundaries(start, end, test_window_days=180))
    estimate = estimate_fold_count(start, end, test_window_days=180)
    assert estimate >= actual

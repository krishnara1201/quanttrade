"""Tests for portfolio-level backtests: basket diversification across
multiple tickers with custom fixed weights, aggregated into one portfolio
equity curve/metrics on top of the existing single-ticker StrategyExecutor.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, PortfolioBacktestResult


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="strat", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        pbr = PortfolioBacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            allocations=[{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}],
            results={"return_pct": 1.0},
            equity_curve=[{"date": "2024-01-01T00:00:00", "equity": 10000.0}],
            per_ticker={"AAPL": {"metrics": {}}},
        )
        db.add(pbr)
        await db.commit()
        await db.refresh(pbr)

        result = await db.execute(
            select(PortfolioBacktestResult).where(PortfolioBacktestResult.id == pbr.id)
        )
        loaded = result.scalars().first()
        assert loaded.allocations == [
            {"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}
        ]
        assert loaded.initial_capital == 10000.0
        assert loaded.commission_pct == 0.1
        assert loaded.per_ticker == {"AAPL": {"metrics": {}}}

import asyncio
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, BacktestResult, PortfolioBacktestResult, MarketData, DataImportJob


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # tasks.py resolves its DB session via worker_db.get_worker_session_factory(),
    # which normally points at Postgres — redirect it at this same in-memory
    # sqlite engine so the task body (running via asyncio.run() inside
    # task_always_eager) hits the same DB the test set up.
    import worker_db
    monkeypatch.setattr(worker_db, "_session_factory", factory)

    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="s", project_id=project.id,
            parameters=json.dumps({
                "name": "s", "parameters": {"fast_ma": 2, "slow_ma": 3},
                "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
            }),
        )
        db.add(strategy)
        await db.flush()

        backtest = BacktestResult(
            strategy_id=strategy.id, ticker="TEST",
            start_date=datetime(2024, 1, 1), end_date=datetime(2024, 1, 6),
            initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05,
        )
        db.add(backtest)

        for i, close in enumerate([10, 11, 12, 13, 14, 15]):
            db.add(MarketData(
                ticker="TEST", date=datetime(2024, 1, i + 1),
                open=str(close), high=str(close), low=str(close), close=str(close), volume="1000",
            ))
        await db.commit()
        return {"strategy_id": strategy.id, "backtest_id": backtest.id}


@pytest.mark.asyncio
async def test_run_backtest_task_marks_row_success(session_factory, seeded):
    from tasks import run_backtest_task
    # task_always_eager runs the task body inline, which itself calls
    # asyncio.run() (see tasks.py) — that can't nest inside this test
    # coroutine's own running event loop, so hand the call off to a worker
    # thread (no running loop there), matching how a real Celery worker
    # process actually invokes it (never from inside a live asyncio loop).
    await asyncio.to_thread(run_backtest_task.delay, seeded["backtest_id"])

    async with session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(BacktestResult).where(BacktestResult.id == seeded["backtest_id"]))
        record = result.scalars().first()

    assert record.status == "success"
    assert record.results["num_trades"] >= 0

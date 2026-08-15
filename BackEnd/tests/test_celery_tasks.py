import asyncio
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, BacktestResult, PortfolioBacktestResult, MarketData, DataImportJob, WalkForwardBacktestResult


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


@pytest_asyncio.fixture
async def import_job(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        job = DataImportJob(user_id=user.id, source="alpha_vantage", ticker="AAPL", status="pending")
        db.add(job)
        await db.commit()
        await db.refresh(job)
        return job.id


@pytest.mark.asyncio
async def test_import_alpha_vantage_task_marks_job_failed_after_retries_exhausted(
    session_factory, import_job, monkeypatch
):
    """Regression test for the dead retry-exhaustion branch: Task.retry(exc=...)
    re-raises the ORIGINAL exception once retries are exhausted rather than
    raising MaxRetriesExceededError, so import_alpha_vantage_task must check
    self.request.retries itself (see tasks.py).

    Under task_always_eager, self.retry() does NOT actually loop and
    re-invoke the task body — it raises celery.exceptions.Retry immediately
    (eager mode has no broker to redeliver the message), which is exactly
    what a real worker's first attempt (retries=0) does before the broker
    redelivers with an incremented retries count. So this test drives the
    two attempts a real worker would make explicitly via Task.apply(...,
    retries=N), which Celery accepts precisely to let a caller set
    self.request.retries: retries=0 (the initial attempt, max_retries=1)
    must retry; retries=1 (the redelivered final attempt) must exhaust and
    mark the job failed, never leaving it stuck at status='running'."""
    import httpx
    from celery.exceptions import Retry
    from tasks import import_alpha_vantage_task

    async def fake_get(self, url, params=None, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)
    args = (import_job, "AAPL", "demo", "compact", None, None)

    # Attempt 1 (retries=0 < max_retries=1): must retry, not fail the job.
    def first_attempt():
        with pytest.raises(Retry):
            import_alpha_vantage_task.apply(args=args, retries=0, throw=True)
    await asyncio.to_thread(first_attempt)

    async with session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(DataImportJob).where(DataImportJob.id == import_job))
        job = result.scalars().first()
    assert job.status == "running"

    # Attempt 2 (retries=1 >= max_retries=1): retries exhausted, must mark failed.
    await asyncio.to_thread(import_alpha_vantage_task.apply, args, {}, None, retries=1, throw=True)

    async with session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(select(DataImportJob).where(DataImportJob.id == import_job))
        job = result.scalars().first()

    assert job.status == "failed"
    assert "after retries" in job.error_message


@pytest_asyncio.fixture
async def walk_forward_seeded(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="ml", project_id=project.id,
            parameters=json.dumps({"name": "ml", "mode": "custom_code"}),
            code=(
                "def generate_signals(df):\n"
                "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
                "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
                "    return up - down\n"
            ),
        )
        db.add(strategy)
        await db.flush()

        wf = WalkForwardBacktestResult(
            strategy_id=strategy.id, ticker="AAPL",
            start_date=datetime(2015, 1, 1), end_date=datetime(2020, 1, 1),
            test_window_days=180, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05,
        )
        db.add(wf)

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close=str(100 + (i % 10)), volume="1000",
            ))
        await db.commit()
        return {"walk_forward_id": wf.id}


@pytest.mark.asyncio
async def test_walk_forward_task_marks_row_success(session_factory, walk_forward_seeded):
    from tasks import walk_forward_task
    await asyncio.to_thread(walk_forward_task.delay, walk_forward_seeded["walk_forward_id"])

    async with session_factory() as db:
        from sqlalchemy import select
        result = await db.execute(
            select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == walk_forward_seeded["walk_forward_id"])
        )
        record = result.scalars().first()

    assert record.status == "success"
    assert record.folds_completed == record.total_folds

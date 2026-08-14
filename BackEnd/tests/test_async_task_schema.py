from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, BacktestResult, PortfolioBacktestResult, DataImportJob


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_backtest_result_has_pending_status_and_input_columns(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="s", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        record = BacktestResult(
            strategy_id=strategy.id,
            ticker="AAPL",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    assert record.status == "pending"
    assert record.error_message is None
    assert record.ticker == "AAPL"


@pytest.mark.asyncio
async def test_portfolio_backtest_result_has_pending_status(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="s", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        record = PortfolioBacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            allocations=[{"ticker": "AAPL", "weight": 1.0}],
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    assert record.status == "pending"
    assert record.error_message is None


@pytest.mark.asyncio
async def test_data_import_job_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        job = DataImportJob(user_id=user.id, source="csv", ticker="AAPL")
        db.add(job)
        await db.commit()
        await db.refresh(job)

    assert job.status == "pending"
    assert job.result is None
    assert job.error_message is None
    assert job.created_at is not None

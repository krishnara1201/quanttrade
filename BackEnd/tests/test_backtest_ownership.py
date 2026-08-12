"""
Regression tests for the known async-lazy-load bug documented in CLAUDE.md:

`strategy.project.owner_id` / `backtest.strategy.project` ownership checks in
services/backtest_service.py and routers/backtest.py lazily traverse SQLAlchemy
relationships with no eager-loading configured. Against a real async session
(here: aiosqlite, standing in for asyncpg) this raises MissingGreenlet because
attribute access is synchronous but the lazy load needs to issue IO.

These tests use a fresh session per "request" (mirroring get_db's
async with AsyncSessionLocal() as session pattern) so relationships are
never pre-populated, and go through the same select()-then-attribute-access
code paths as production.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, BacktestResult, MarketData
from services import backtest_service
from routers import backtest as backtest_router


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session_factory):
    """Create a user/project/strategy/backtest and return their ids."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()

        strategy = Strategy(
            name="strat",
            project_id=project.id,
            parameters=json.dumps(
                {
                    "name": "strat",
                    "parameters": {"fast_ma": 2, "slow_ma": 3},
                    "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
                }
            ),
        )
        db.add(strategy)
        await db.flush()

        backtest = BacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            results={},
            trades=[],
            signals=[],
            equity_curve=[],
        )
        db.add(backtest)

        for i, close in enumerate([10, 11, 12, 13, 14, 15]):
            db.add(
                MarketData(
                    ticker="TEST",
                    date=datetime(2024, 1, i + 1),
                    open=str(close),
                    high=str(close),
                    low=str(close),
                    close=str(close),
                    volume="1000",
                )
            )

        await db.commit()
        return {
            "user_id": user.id,
            "project_id": project.id,
            "strategy_id": strategy.id,
            "backtest_id": backtest.id,
        }


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_run_backtest_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        response = await backtest_service.run_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
        )
    assert response["strategy_id"] == seeded["strategy_id"]


@pytest.mark.asyncio
async def test_run_backtest_queries_market_data_with_datetime_not_raw_string(session_factory, seeded, monkeypatch):
    """Regression for a prod-only bug: start_date/end_date arrive as plain
    strings ("2024-01-01"). SQLite's DateTime bind processor passes a raw str
    straight through unmodified, so `MarketData.date >= "2024-01-01"` quietly
    "works" here — but Postgres/asyncpg binds it as ::VARCHAR and rejects
    `timestamp >= varchar` outright (UndefinedFunctionError), 500ing every
    real backtest run. This only catches the type of value SQLAlchemy binds
    into the query (independent of dialect), which is where the bug actually
    lives, since a SQLite-only test can't reproduce the Postgres error itself."""
    captured_params = []

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        original_execute = db.execute

        async def spying_execute(stmt, *args, **kwargs):
            if hasattr(stmt, "compile"):
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                captured_params.append(dict(compiled.params))
            return await original_execute(stmt, *args, **kwargs)

        monkeypatch.setattr(db, "execute", spying_execute)

        await backtest_service.run_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
        )

    all_values = [v for params in captured_params for v in params.values()]
    assert "2024-01-01" not in all_values, "start_date leaked into the query as a raw string"
    assert "2024-01-06" not in all_values, "end_date leaked into the query as a raw string"
    assert any(
        isinstance(v, datetime) and v == datetime(2024, 1, 1) for v in all_values
    ), "expected start_date to be bound as a parsed datetime"


@pytest.mark.asyncio
async def test_get_backtest_results_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        results = await backtest_service.get_backtest_results(
            seeded["strategy_id"], db=db, user=user,
        )
    assert len(results) == 1
    assert results[0]["strategy_id"] == seeded["strategy_id"]


@pytest.mark.asyncio
async def test_get_backtest_detail_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        detail = await backtest_router.get_backtest_detail(
            seeded["backtest_id"], db=db, user=user,
        )
    assert detail["id"] == seeded["backtest_id"]

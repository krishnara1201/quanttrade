"""
Regression test for the same async-lazy-load bug covered in
test_backtest_ownership.py, this time in routers/strategies.py's
update_strategy: `strategy.project.owner_id` is accessed without eager
loading, which raises MissingGreenlet against a real async session.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from database.models import Base, User, Project, Strategy, BacktestResult
from routers import strategies as strategies_router


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
            parameters=json.dumps({"name": "strat", "parameters": {}, "rules": {}}),
        )
        db.add(strategy)
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


@pytest.mark.asyncio
async def test_update_strategy_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        user = result.scalars().first()
        updated = await strategies_router.update_strategy(
            seeded["strategy_id"], {"name": "renamed"}, db=db, user=user,
        )
    assert updated.name == "renamed"


@pytest_asyncio.fixture
async def seeded_with_backtest(session_factory, seeded):
    """Adds a BacktestResult under the seeded strategy plus a second,
    unrelated user, to exercise cascade-delete and the 403 path."""
    async with session_factory() as db:
        backtest = BacktestResult(
            strategy_id=seeded["strategy_id"],
            ticker="TEST",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            status="success",
            results={}, trades=[], signals=[], equity_curve=[],
        )
        db.add(backtest)

        other_user = User(name="Bob", email="bob@example.com", password_hash="x")
        db.add(other_user)
        await db.commit()
        await db.refresh(backtest)
        await db.refresh(other_user)
        return {**seeded, "backtest_id": backtest.id, "other_user_id": other_user.id}


@pytest.mark.asyncio
async def test_delete_strategy_does_not_raise_missing_greenlet_and_cascades(session_factory, seeded_with_backtest):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded_with_backtest["user_id"]))
        user = result.scalars().first()
        response = await strategies_router.delete_strategy(
            seeded_with_backtest["strategy_id"], db=db, user=user,
        )
    assert response == {"detail": "Strategy deleted"}

    async with session_factory() as db:
        strategy_result = await db.execute(select(Strategy).where(Strategy.id == seeded_with_backtest["strategy_id"]))
        assert strategy_result.scalars().first() is None

        backtest_result = await db.execute(select(BacktestResult).where(BacktestResult.id == seeded_with_backtest["backtest_id"]))
        assert backtest_result.scalars().first() is None


@pytest.mark.asyncio
async def test_delete_strategy_403s_for_non_owner(session_factory, seeded_with_backtest):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded_with_backtest["other_user_id"]))
        other_user = result.scalars().first()
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.delete_strategy(
                seeded_with_backtest["strategy_id"], db=db, user=other_user,
            )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_strategy_404s_for_unknown_id(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        user = result.scalars().first()
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.delete_strategy(999999, db=db, user=user)
    assert exc_info.value.status_code == 404

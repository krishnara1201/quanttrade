"""
Tests for DELETE /api/projects/{project_id} — verifies the ownership check
(project.owner_id != user.id -> 403, unknown id -> 404) and that deleting a
project cascades to its strategies and, transitively, their backtest rows
(Project.strategies / Strategy.backtests both declare
cascade="all, delete-orphan" in database/models.py), all against a real
async session (sqlite+aiosqlite standing in for asyncpg) to catch the same
class of MissingGreenlet regression documented in CLAUDE.md.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from database.models import Base, User, Project, Strategy, BacktestResult
from routers import projects as projects_router


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
    """One owner with a project+strategy+backtest, plus an unrelated user."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        other_user = User(name="Bob", email="bob@example.com", password_hash="x")
        db.add_all([user, other_user])
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
        await db.flush()

        backtest = BacktestResult(
            strategy_id=strategy.id,
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
        await db.commit()

        return {
            "user_id": user.id,
            "other_user_id": other_user.id,
            "project_id": project.id,
            "strategy_id": strategy.id,
            "backtest_id": backtest.id,
        }


@pytest.mark.asyncio
async def test_delete_project_cascades_to_strategies_and_backtests(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        user = result.scalars().first()
        response = await projects_router.delete_project(
            seeded["project_id"], db=db, user=user,
        )
    assert response == {"detail": "Project deleted"}

    async with session_factory() as db:
        project_result = await db.execute(select(Project).where(Project.id == seeded["project_id"]))
        assert project_result.scalars().first() is None

        strategy_result = await db.execute(select(Strategy).where(Strategy.id == seeded["strategy_id"]))
        assert strategy_result.scalars().first() is None

        backtest_result = await db.execute(select(BacktestResult).where(BacktestResult.id == seeded["backtest_id"]))
        assert backtest_result.scalars().first() is None


@pytest.mark.asyncio
async def test_delete_project_403s_for_non_owner(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["other_user_id"]))
        other_user = result.scalars().first()
        with pytest.raises(HTTPException) as exc_info:
            await projects_router.delete_project(
                seeded["project_id"], db=db, user=other_user,
            )
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_delete_project_404s_for_unknown_id(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        user = result.scalars().first()
        with pytest.raises(HTTPException) as exc_info:
            await projects_router.delete_project(999999, db=db, user=user)
    assert exc_info.value.status_code == 404

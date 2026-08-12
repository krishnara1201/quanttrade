"""
Save-time AST safety validation for custom_code-mode strategies in
routers/strategies.py's create_strategy and update_strategy. Uses a real
in-memory sqlite+aiosqlite async session and calls the router functions
directly (no HTTP layer, no mocks), matching test_strategy_ownership.py.
"""
import json

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy
from routers import strategies as strategies_router


VALID_CODE = "def generate_signals(df):\n    return df['close'] * 0\n"
UNSAFE_CODE = "import os\ndef generate_signals(df):\n    return df['close'] * 0\n"


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
        await db.commit()
        return {"user_id": user.id, "project_id": project.id}


async def _get_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_create_custom_code_strategy_with_valid_code_succeeds(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy_data = strategies_router.StrategyCreate(
            name="my-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({"name": "my-strategy", "mode": "custom_code"}),
            code=VALID_CODE,
        )
        created = await strategies_router.create_strategy(strategy_data, db=db, user=user)
    assert created.code == VALID_CODE


@pytest.mark.asyncio
async def test_create_custom_code_strategy_with_empty_code_rejected(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy_data = strategies_router.StrategyCreate(
            name="my-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({"name": "my-strategy", "mode": "custom_code"}),
            code="",
        )
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.create_strategy(strategy_data, db=db, user=user)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_create_custom_code_strategy_with_disallowed_import_rejected(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy_data = strategies_router.StrategyCreate(
            name="my-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({"name": "my-strategy", "mode": "custom_code"}),
            code=UNSAFE_CODE,
        )
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.create_strategy(strategy_data, db=db, user=user)
    assert exc_info.value.status_code == 400
    assert "os" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_rules_mode_strategy_unaffected_by_code_validation(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy_data = strategies_router.StrategyCreate(
            name="rules-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({
                "name": "rules-strategy",
                "parameters": {"fast_ma": 5, "slow_ma": 10},
                "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
            }),
        )
        created = await strategies_router.create_strategy(strategy_data, db=db, user=user)
    assert created.code is None


@pytest.mark.asyncio
async def test_update_strategy_code_to_unsafe_code_rejected(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy = Strategy(
            name="my-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({"name": "my-strategy", "mode": "custom_code"}),
            code=VALID_CODE,
        )
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)
        strategy_id = strategy.id

    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        with pytest.raises(HTTPException) as exc_info:
            await strategies_router.update_strategy(
                strategy_id, {"code": UNSAFE_CODE}, db=db, user=user,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_update_strategy_code_to_valid_code_succeeds(session_factory, seeded):
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        strategy = Strategy(
            name="my-strategy",
            project_id=seeded["project_id"],
            parameters=json.dumps({"name": "my-strategy", "mode": "custom_code"}),
            code=VALID_CODE,
        )
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)
        strategy_id = strategy.id

    new_code = "def generate_signals(df):\n    return df['close'] * 0 + 1\n"
    async with session_factory() as db:
        user = await _get_user(session_factory, seeded["user_id"])
        updated = await strategies_router.update_strategy(
            strategy_id, {"code": new_code}, db=db, user=user,
        )
    assert updated.code == new_code

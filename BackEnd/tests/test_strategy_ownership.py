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
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select

from database.models import Base, User, Project, Strategy
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

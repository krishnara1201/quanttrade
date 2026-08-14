"""
Tests for DELETE /api/data/{ticker}/all — bulk-deletes every MarketData row
for a ticker in one action, added alongside the market-data visualization UI
so the "Data on file" list can offer a per-ticker delete rather than only the
existing single-row DELETE /{data_id} endpoint (impractical for thousands of
bars).
"""
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, MarketData
from routers import data as data_router


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
    """One user plus AAPL (3 bars) and MSFT (1 bar)."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        for i in (1, 2, 3):
            db.add(MarketData(
                ticker="AAPL", date=datetime(2024, 1, i),
                open="1", high="1", low="1", close="1", volume="1000",
            ))
        db.add(MarketData(
            ticker="MSFT", date=datetime(2024, 2, 1),
            open="300", high="300", low="300", close="300", volume="500",
        ))

        await db.commit()
        return {"user_id": user.id}


@pytest_asyncio.fixture
async def user(session_factory, seeded):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_delete_ticker_data_removes_only_that_tickers_rows(session_factory, user):
    async with session_factory() as db:
        result = await data_router.delete_ticker_data("AAPL", db=db, current_user=user)

    assert result == {"ticker": "AAPL", "deleted": 3}

    async with session_factory() as db:
        remaining = await db.execute(select(MarketData.ticker))
        tickers = remaining.scalars().all()
    assert tickers == ["MSFT"]


@pytest.mark.asyncio
async def test_delete_ticker_data_404s_for_unknown_ticker(session_factory, user):
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.delete_ticker_data("NOPE", db=db, current_user=user)
    assert exc_info.value.status_code == 404

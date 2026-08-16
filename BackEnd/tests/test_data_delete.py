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


@pytest_asyncio.fixture
async def other_user(session_factory):
    async with session_factory() as db:
        u = User(name="Bea", email="bea@example.com", password_hash="x")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest.mark.asyncio
async def test_delete_market_data_removes_own_row(session_factory, user):
    async with session_factory() as db:
        row = MarketData(
            ticker="TSLA", date=datetime(2024, 3, 1),
            open="1", high="1", low="1", close="1", volume="1", imported_by=user.id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        row_id = row.id

    async with session_factory() as db:
        result = await data_router.delete_market_data(row_id, db=db, current_user=user)
    assert result == {"detail": "Market data deleted"}


@pytest.mark.asyncio
async def test_delete_market_data_403s_for_another_users_row(session_factory, user, other_user):
    async with session_factory() as db:
        row = MarketData(
            ticker="TSLA", date=datetime(2024, 3, 1),
            open="1", high="1", low="1", close="1", volume="1", imported_by=other_user.id,
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        row_id = row.id

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.delete_market_data(row_id, db=db, current_user=user)
    assert exc_info.value.status_code == 403

    async with session_factory() as db:
        still_there = (await db.execute(select(MarketData).where(MarketData.id == row_id))).scalars().first()
    assert still_there is not None


@pytest.mark.asyncio
async def test_delete_market_data_allows_deleting_legacy_row_with_no_importer(session_factory, user):
    """Rows imported before imported_by existed have imported_by IS NULL and
    stay deletable by any authenticated user, matching pre-existing behavior."""
    async with session_factory() as db:
        row = MarketData(
            ticker="TSLA", date=datetime(2024, 3, 1),
            open="1", high="1", low="1", close="1", volume="1",
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
        row_id = row.id

    async with session_factory() as db:
        result = await data_router.delete_market_data(row_id, db=db, current_user=user)
    assert result == {"detail": "Market data deleted"}


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
async def test_delete_ticker_data_only_removes_own_and_legacy_rows(session_factory, user, other_user):
    """AAPL has 3 legacy rows (imported_by NULL, from the `seeded` fixture)
    plus one row imported by other_user and one imported by `user` itself —
    the bulk delete should remove the legacy + own rows but leave
    other_user's row untouched."""
    async with session_factory() as db:
        db.add(MarketData(
            ticker="AAPL", date=datetime(2024, 1, 10),
            open="1", high="1", low="1", close="1", volume="1", imported_by=other_user.id,
        ))
        db.add(MarketData(
            ticker="AAPL", date=datetime(2024, 1, 11),
            open="1", high="1", low="1", close="1", volume="1", imported_by=user.id,
        ))
        await db.commit()

    async with session_factory() as db:
        result = await data_router.delete_ticker_data("AAPL", db=db, current_user=user)
    assert result == {"ticker": "AAPL", "deleted": 4}  # 3 legacy + 1 own

    async with session_factory() as db:
        remaining = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].imported_by == other_user.id


@pytest.mark.asyncio
async def test_delete_ticker_data_404s_for_unknown_ticker(session_factory, user):
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.delete_ticker_data("NOPE", db=db, current_user=user)
    assert exc_info.value.status_code == 404

"""
Tests for the historical-data read endpoints in routers/data.py added to
support a ticker dropdown + a backtest-ready data window:

- GET /api/data/tickers        -> distinct tickers, alphabetically ordered
- GET /api/data/{ticker}/range -> earliest/latest date + row count for a ticker,
                                   so a caller can constrain a date picker to a
                                   range that actually has data
- GET /api/data/{ticker}/historical -> now supports optional start_date/end_date
                                        filtering and is always ordered by date,
                                        mirroring the query backtest_service.py
                                        issues when it loads data for a run
"""
from datetime import datetime

import pytest
import pytest_asyncio
from fastapi import HTTPException
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
    """One user plus AAPL (5 daily bars, seeded out of order) and MSFT (1 bar)."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        for i, close in [(3, 13), (1, 11), (5, 15), (2, 12), (4, 14)]:
            db.add(MarketData(
                ticker="AAPL", date=datetime(2024, 1, i),
                open=str(close), high=str(close), low=str(close),
                close=str(close), volume="1000",
            ))
        db.add(MarketData(
            ticker="MSFT", date=datetime(2024, 2, 1),
            open="300", high="300", low="300", close="300", volume="500",
        ))

        await db.commit()
        return {"user_id": user.id}


@pytest_asyncio.fixture
async def user(session_factory, seeded):
    from sqlalchemy import select
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == seeded["user_id"]))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_get_tickers_returns_distinct_sorted_list(session_factory, user):
    async with session_factory() as db:
        tickers = await data_router.get_tickers(db=db, current_user=user)
    assert tickers == ["AAPL", "MSFT"]


@pytest.mark.asyncio
async def test_get_ticker_range_reports_min_max_and_count(session_factory, user):
    async with session_factory() as db:
        result = await data_router.get_ticker_range("AAPL", db=db, current_user=user)
    assert result["ticker"] == "AAPL"
    assert result["start_date"] == datetime(2024, 1, 1)
    assert result["end_date"] == datetime(2024, 1, 5)
    assert result["count"] == 5


@pytest.mark.asyncio
async def test_get_ticker_range_reports_deletable_count(session_factory, user):
    """AAPL has 5 legacy (imported_by NULL) bars from the `seeded` fixture —
    all deletable by any user, including the current one."""
    async with session_factory() as db:
        result = await data_router.get_ticker_range("AAPL", db=db, current_user=user)
    assert result["count"] == 5
    assert result["deletable_count"] == 5


@pytest.mark.asyncio
async def test_get_ticker_range_deletable_count_excludes_other_users_rows(session_factory, user):
    from sqlalchemy import select
    async with session_factory() as db:
        other = User(name="Bea", email="bea@example.com", password_hash="x")
        db.add(other)
        await db.flush()
        db.add(MarketData(
            ticker="AAPL", date=datetime(2024, 1, 6),
            open="16", high="16", low="16", close="16", volume="1000",
            imported_by=other.id,
        ))
        await db.commit()

    async with session_factory() as db:
        result = await data_router.get_ticker_range("AAPL", db=db, current_user=user)
    assert result["count"] == 6
    assert result["deletable_count"] == 5


@pytest.mark.asyncio
async def test_get_ticker_range_404s_for_unknown_ticker(session_factory, user):
    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.get_ticker_range("NOPE", db=db, current_user=user)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_historical_data_is_ordered_chronologically(session_factory, user):
    async with session_factory() as db:
        rows = await data_router.get_historical_data(
            "AAPL", start_date=None, end_date=None, db=db, current_user=user,
        )
    assert [r.date for r in rows] == [
        datetime(2024, 1, 1), datetime(2024, 1, 2), datetime(2024, 1, 3),
        datetime(2024, 1, 4), datetime(2024, 1, 5),
    ]


@pytest.mark.asyncio
async def test_get_historical_data_filters_by_date_window(session_factory, user):
    async with session_factory() as db:
        rows = await data_router.get_historical_data(
            "AAPL",
            start_date=datetime(2024, 1, 2), end_date=datetime(2024, 1, 4),
            db=db, current_user=user,
        )
    assert [r.date for r in rows] == [
        datetime(2024, 1, 2), datetime(2024, 1, 3), datetime(2024, 1, 4),
    ]


@pytest.mark.asyncio
async def test_get_historical_data_matches_what_a_backtest_would_load(session_factory, user):
    """The windowed query here should return exactly the rows
    services/backtest_service.run_backtest would load for the same range."""
    async with session_factory() as db:
        rows = await data_router.get_historical_data(
            "AAPL",
            start_date=datetime(2024, 1, 2), end_date=datetime(2024, 1, 4),
            db=db, current_user=user,
        )
    assert len(rows) == 3
    assert [float(r.close) for r in rows] == [12.0, 13.0, 14.0]

"""
Regression tests for the field-mismatch bug in routers/data.py:
`MarketDataCreate` used to declare `symbol`/`timestamp`, which don't exist as
columns on the `MarketData` model (`ticker`/`date`). `MarketData(**data.dict())`
therefore raised TypeError on every upload. The schema now mirrors the model's
columns directly, with OHLCV values cast to str() before construction since
those columns are typed String.
"""
from datetime import datetime

import pytest
import pytest_asyncio
from pydantic import ValidationError
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
async def user(session_factory):
    async with session_factory() as db:
        u = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u


@pytest.mark.asyncio
async def test_upload_market_data_persists_correct_fields(session_factory, user):
    payload = data_router.MarketDataCreate(
        ticker="AAPL",
        date=datetime(2024, 1, 2),
        open=185.5,
        high=186.0,
        low=184.75,
        close=185.9,
        volume=1000000,
    )

    async with session_factory() as db:
        created = await data_router.upload_market_data(payload, db=db, current_user=user)

    assert created.id is not None
    assert created.ticker == "AAPL"
    assert created.date == datetime(2024, 1, 2)
    assert float(created.open) == 185.5
    assert float(created.high) == 186.0
    assert float(created.low) == 184.75
    assert float(created.close) == 185.9
    assert float(created.volume) == 1000000
    assert created.adj_close is None


@pytest.mark.asyncio
async def test_uploaded_market_data_is_queryable_by_ticker(session_factory, user):
    payload = data_router.MarketDataCreate(
        ticker="AAPL", date=datetime(2024, 1, 2),
        open=1, high=1, low=1, close=1, volume=1,
    )
    async with session_factory() as db:
        await data_router.upload_market_data(payload, db=db, current_user=user)

    async with session_factory() as db:
        rows = await data_router.get_historical_data("AAPL", db=db, current_user=user)
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"


@pytest.mark.asyncio
async def test_upload_market_data_persists_optional_adj_close(session_factory, user):
    payload = data_router.MarketDataCreate(
        ticker="AAPL", date=datetime(2024, 1, 2),
        open=1, high=1, low=1, close=1, volume=1, adj_close=0.99,
    )
    async with session_factory() as db:
        created = await data_router.upload_market_data(payload, db=db, current_user=user)
    assert float(created.adj_close) == 0.99


def test_market_data_create_rejects_legacy_symbol_timestamp_fields():
    """The pre-fix schema used symbol/timestamp; those must no longer validate."""
    with pytest.raises(ValidationError):
        data_router.MarketDataCreate(
            symbol="AAPL", timestamp="2024-01-02",
            open=1, high=1, low=1, close=1, volume=1,
        )

"""
Tests for bulk market-data import in routers/data.py:

- POST /api/data/upload-csv    -> parse an uploaded CSV and bulk-insert bars
- POST /api/data/import/{ticker} -> fetch daily OHLCV from Stooq (mocked here,
                                     no real network call) and bulk-insert bars

Both share `_bulk_upsert_market_data`, which is exercised directly for
column-parsing/duplicate-skipping behavior, plus once through each endpoint.
"""
import io
from datetime import datetime

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
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


# ---- _bulk_upsert_market_data ----------------------------------------------

@pytest.mark.asyncio
async def test_bulk_upsert_inserts_new_bars(session_factory):
    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
        {"date": "2024-01-03", "open": 1.5, "high": 2.5, "low": 1, "close": 2, "volume": 200},
    ])
    async with session_factory() as db:
        result = await data_router._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 2, "skipped": 0}


@pytest.mark.asyncio
async def test_bulk_upsert_skips_bars_already_stored(session_factory):
    async with session_factory() as db:
        db.add(MarketData(
            ticker="AAPL", date=datetime(2024, 1, 2),
            open="1", high="2", low="0.5", close="1.5", volume="100",
        ))
        await db.commit()

    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},  # dup
        {"date": "2024-01-03", "open": 1.5, "high": 2.5, "low": 1, "close": 2, "volume": 200},  # new
    ])
    async with session_factory() as db:
        result = await data_router._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 1, "skipped": 1}

    async with session_factory() as db:
        rows = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_bulk_upsert_rejects_missing_columns(session_factory):
    df = pd.DataFrame([{"date": "2024-01-02", "open": 1, "close": 1.5}])
    async with session_factory() as db:
        with pytest.raises(ValueError, match="missing required column"):
            await data_router._bulk_upsert_market_data("AAPL", df, db)


@pytest.mark.asyncio
async def test_bulk_upsert_normalizes_column_names_and_keeps_adj_close(session_factory):
    df = pd.DataFrame([
        {"Date": "2024-01-02", "Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 100, "Adj Close": 1.49},
    ])
    async with session_factory() as db:
        result = await data_router._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 1, "skipped": 0}

    async with session_factory() as db:
        row = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().first()
    assert float(row.adj_close) == 1.49


# ---- POST /api/data/upload-csv ---------------------------------------------

@pytest.mark.asyncio
async def test_upload_market_data_csv_inserts_bars(session_factory, user):
    csv_bytes = (
        b"Date,Open,High,Low,Close,Volume\n"
        b"2024-01-02,185.5,186.0,184.75,185.9,1000000\n"
        b"2024-01-03,186.0,187.0,185.5,186.5,900000\n"
    )
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="aapl.csv")

    async with session_factory() as db:
        result = await data_router.upload_market_data_csv(
            ticker="aapl", file=upload, db=db, current_user=user,
        )
    assert result == {"ticker": "AAPL", "inserted": 2, "skipped": 0}


@pytest.mark.asyncio
async def test_upload_market_data_csv_rejects_malformed_columns(session_factory, user):
    csv_bytes = b"Foo,Bar\n1,2\n"
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="bad.csv")

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.upload_market_data_csv(
                ticker="aapl", file=upload, db=db, current_user=user,
            )
    assert exc_info.value.status_code == 400


# ---- POST /api/data/import/{ticker} (Alpha Vantage) ------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_import_market_data_from_web_inserts_bars(session_factory, user, monkeypatch):
    payload = {
        "Time Series (Daily)": {
            "2024-01-03": {"1. open": "186.0", "2. high": "187.0", "3. low": "185.5", "4. close": "186.5", "5. volume": "900000"},
            "2024-01-02": {"1. open": "185.5", "2. high": "186.0", "3. low": "184.75", "4. close": "185.9", "5. volume": "1000000"},
        }
    }

    captured = {}

    async def fake_get(self, url, params=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        result = await data_router.import_market_data_from_web(
            "aapl", start_date=None, end_date=None, db=db, current_user=user,
        )
    assert result == {"ticker": "AAPL", "inserted": 2, "skipped": 0}
    assert captured["params"]["symbol"] == "aapl"
    assert captured["params"]["function"] == "TIME_SERIES_DAILY"


@pytest.mark.asyncio
async def test_import_market_data_from_web_filters_to_date_window(session_factory, user, monkeypatch):
    payload = {
        "Time Series (Daily)": {
            "2024-01-01": {"1. open": "1", "2. high": "1", "3. low": "1", "4. close": "1", "5. volume": "1"},
            "2024-01-02": {"1. open": "2", "2. high": "2", "3. low": "2", "4. close": "2", "5. volume": "2"},
            "2024-01-05": {"1. open": "3", "2. high": "3", "3. low": "3", "4. close": "3", "5. volume": "3"},
        }
    }

    async def fake_get(self, url, params=None):
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        result = await data_router.import_market_data_from_web(
            "aapl", start_date=datetime(2024, 1, 2), end_date=datetime(2024, 1, 2), db=db, current_user=user,
        )
    assert result == {"ticker": "AAPL", "inserted": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_import_market_data_from_web_404s_on_invalid_symbol(session_factory, user, monkeypatch):
    async def fake_get(self, url, params=None):
        return _FakeResponse({"Error Message": "Invalid API call. Please retry or visit the documentation."})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.import_market_data_from_web(
                "NOTATICKER", start_date=None, end_date=None, db=db, current_user=user,
            )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_import_market_data_from_web_400s_when_key_is_unset_or_limited(session_factory, user, monkeypatch):
    """Demo-key / rate-limit responses come back as 200 with an
    Information/Note field instead of Time Series data — surface that as a
    400 with Alpha Vantage's own message, not a generic failure."""
    async def fake_get(self, url, params=None):
        return _FakeResponse({"Information": "The **demo** API key is for demo purposes only."})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.import_market_data_from_web(
                "AAPL", start_date=None, end_date=None, db=db, current_user=user,
            )
    assert exc_info.value.status_code == 400
    assert "demo" in exc_info.value.detail.lower()


@pytest.mark.asyncio
async def test_import_market_data_from_web_uses_explicit_api_key_over_env(session_factory, user, monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "env-key")
    captured = {}

    async def fake_get(self, url, params=None):
        captured["params"] = params
        return _FakeResponse({"Error Message": "nope"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        with pytest.raises(HTTPException):
            await data_router.import_market_data_from_web(
                "AAPL", start_date=None, end_date=None, api_key="explicit-key", db=db, current_user=user,
            )
    assert captured["params"]["apikey"] == "explicit-key"

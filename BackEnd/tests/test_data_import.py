"""
Tests for bulk market-data import in routers/data.py:

- POST /api/data/upload-csv    -> parse an uploaded CSV and bulk-insert bars
- POST /api/data/import/{ticker} -> fetch daily OHLCV from Stooq (mocked here,
                                     no real network call) and bulk-insert bars

Both share `_bulk_upsert_market_data`, which is exercised directly for
column-parsing/duplicate-skipping behavior, plus once through each endpoint.
"""
import concurrent.futures
import io
from datetime import datetime

import httpx
import pandas as pd
import pytest
import pytest_asyncio
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, MarketData, DataImportJob
from routers import data as data_router
from services import data_import_service


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # upload_market_data_csv/import_market_data_from_web (post-Task-8) enqueue
    # Celery tasks. Under task_always_eager that task body resolves its own DB
    # session via worker_db.get_worker_session_factory() (normally pointed at
    # Postgres) rather than this fixture's session — redirect it at this same
    # in-memory sqlite engine, mirroring tests/test_portfolio_backtest_service.py.
    import worker_db
    monkeypatch.setattr(worker_db, "_session_factory", factory)

    yield factory
    await engine.dispose()


def _run_task_delay_in_new_thread(monkeypatch, task):
    """upload_market_data_csv/import_market_data_from_web call `.delay(...)`
    synchronously from inside these awaited endpoint functions. Under
    task_always_eager that runs the task body inline in the SAME thread, and
    the task body calls asyncio.run() (see tasks.py) — which can't nest
    inside the event loop already driving the test coroutine. So instead,
    patch just the .delay() call site to hand off to a brand-new OS thread
    with no running loop of its own — matching exactly how a real Celery
    worker process invokes it (a separate process, never inside a live
    asyncio loop) — while leaving everything else (the real task body, the
    real asyncio.run(), the real execute_*_import()) untouched. This is
    test-only scaffolding; routers/data.py itself is unmodified."""
    original_delay = task.delay

    def delay_in_new_thread(*args, **kwargs):
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(original_delay, *args, **kwargs).result()

    monkeypatch.setattr(task, "delay", delay_in_new_thread)


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
        result = await data_import_service._bulk_upsert_market_data("AAPL", df, db)
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
        result = await data_import_service._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 1, "skipped": 1}

    async with session_factory() as db:
        rows = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().all()
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_bulk_upsert_records_importer(session_factory):
    df = pd.DataFrame([
        {"date": "2024-01-02", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
    ])
    async with session_factory() as db:
        await data_import_service._bulk_upsert_market_data("AAPL", df, db, imported_by=42)

    async with session_factory() as db:
        row = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().first()
    assert row.imported_by == 42


@pytest.mark.asyncio
async def test_bulk_upsert_rejects_missing_columns(session_factory):
    df = pd.DataFrame([{"date": "2024-01-02", "open": 1, "close": 1.5}])
    async with session_factory() as db:
        with pytest.raises(ValueError, match="missing required column"):
            await data_import_service._bulk_upsert_market_data("AAPL", df, db)


@pytest.mark.asyncio
async def test_bulk_upsert_normalizes_column_names_and_keeps_adj_close(session_factory):
    df = pd.DataFrame([
        {"Date": "2024-01-02", "Open": 1, "High": 2, "Low": 0.5, "Close": 1.5, "Volume": 100, "Adj Close": 1.49},
    ])
    async with session_factory() as db:
        result = await data_import_service._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 1, "skipped": 0}

    async with session_factory() as db:
        row = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().first()
    assert float(row.adj_close) == 1.49


# ---- POST /api/data/upload-csv ---------------------------------------------

@pytest.mark.asyncio
async def test_upload_market_data_csv_inserts_bars(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    csv_bytes = (
        b"Date,Open,High,Low,Close,Volume\n"
        b"2024-01-02,185.5,186.0,184.75,185.9,1000000\n"
        b"2024-01-03,186.0,187.0,185.5,186.5,900000\n"
    )
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="aapl.csv")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker="aapl", file=upload, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert job["result"] == {"ticker": "AAPL", "inserted": 2, "skipped": 0}


@pytest.mark.asyncio
async def test_upload_market_data_csv_records_importer(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    csv_bytes = b"Date,Open,High,Low,Close,Volume\n2024-01-02,185.5,186.0,184.75,185.9,1000000\n"
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="aapl.csv")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker="aapl", file=upload, db=db, current_user=user,
        )
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)
    assert job["status"] == "success"

    async with session_factory() as db:
        row = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().first()
    assert row.imported_by == user.id


@pytest.mark.asyncio
async def test_upload_market_data_csv_rejects_malformed_columns(session_factory, user, monkeypatch):
    """Deviates from the task-8 brief, which claimed this test needs no
    changes. In fact _split_bar_groups only validates required OHLCV
    columns for the Stooq-shaped branch (see
    services/data_import_service.py) — a plain header'd CSV with the wrong
    columns parses fine synchronously and only fails column validation
    inside _bulk_upsert_market_data, which (per this task's rewrite) now
    runs inside the async task, not the endpoint. So a malformed plain CSV
    is no longer an immediate 400 from the POST — it surfaces as job
    status="failed" on the follow-up GET, same as the Alpha Vantage
    failure-mode tests below."""
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    csv_bytes = b"Foo,Bar\n1,2\n"
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="bad.csv")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker="aapl", file=upload, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "failed"
    assert "missing required column" in job["error_message"]


@pytest.mark.asyncio
async def test_upload_market_data_csv_rejects_oversized_file(session_factory, user, monkeypatch):
    """upload-csv base64-encodes the whole file into a single Celery task
    argument passed through Redis. Without a size cap, a large-enough upload
    could degrade/crash the broker (Redis's default proto-max-bulk-len is
    512MB) rather than failing cleanly with a 4xx. Shrink the module's limit
    for the test so we don't need to actually build a 50MB+ file."""
    monkeypatch.setattr(data_router, "MAX_UPLOAD_BYTES", 10)
    csv_bytes = b"Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,100\n"
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="aapl.csv")

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.upload_market_data_csv(
                ticker="aapl", file=upload, db=db, current_user=user,
            )
    assert exc_info.value.status_code == 413


@pytest.mark.asyncio
async def test_upload_market_data_csv_requires_ticker_for_plain_csv(session_factory, user):
    csv_bytes = b"Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,100\n"
    upload = UploadFile(file=io.BytesIO(csv_bytes), filename="aapl.csv")

    async with session_factory() as db:
        with pytest.raises(HTTPException) as exc_info:
            await data_router.upload_market_data_csv(
                ticker=None, file=upload, db=db, current_user=user,
            )
    assert exc_info.value.status_code == 400
    assert "ticker is required" in exc_info.value.detail


# ---- YYYYMMDD date parsing regression ---------------------------------------
# Plain-integer YYYYMMDD dates (e.g. Excel/Stooq-style exports store
# 19840907, not "1984-09-07") used to be misread by pd.to_datetime as
# nanoseconds-since-epoch, landing every row within a fraction of a second of
# 1970-01-01 instead of its real date.

@pytest.mark.asyncio
async def test_bulk_upsert_parses_yyyymmdd_integer_dates(session_factory):
    df = pd.DataFrame([
        {"date": 19840907, "open": 0.099173, "high": 0.10039, "low": 0.097975, "close": 0.099173, "volume": 99242379},
        {"date": 19840910, "open": 0.099173, "high": 0.099477, "low": 0.096788, "close": 0.098584, "volume": 77028276},
    ])
    async with session_factory() as db:
        result = await data_import_service._bulk_upsert_market_data("AAPL", df, db)
    assert result == {"ticker": "AAPL", "inserted": 2, "skipped": 0}

    async with session_factory() as db:
        rows = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().all()
    dates = sorted(r.date for r in rows)
    assert dates == [datetime(1984, 9, 7), datetime(1984, 9, 10)]


# ---- Stooq-style headerless per-symbol .txt import --------------------------

@pytest.mark.asyncio
async def test_upload_market_data_csv_infers_ticker_from_stooq_txt(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    txt_bytes = (
        b"AAPL.US,D,20260604,000000,313.23,313.54,309.65,311.23,44869134,0\n"
        b"AAPL.US,D,20260605,000000,312.86,315.17,307.15,307.34,65310502,0\n"
    )
    upload = UploadFile(file=io.BytesIO(txt_bytes), filename="aapl.us.txt")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker=None, file=upload, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert job["result"] == {"ticker": "AAPL", "inserted": 2, "skipped": 0}

    async with session_factory() as db:
        rows = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().all()
    assert sorted(r.date for r in rows) == [datetime(2026, 6, 4), datetime(2026, 6, 5)]


@pytest.mark.asyncio
async def test_upload_market_data_csv_infers_ticker_from_stooq_header(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    txt_bytes = (
        b"<TICKER>,<PER>,<DATE>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>,<OPENINT>\n"
        b"MSFT.US,D,20260604,000000,313.23,313.54,309.65,311.23,44869134,0\n"
    )
    upload = UploadFile(file=io.BytesIO(txt_bytes), filename="msft.us.txt")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker=None, file=upload, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert job["result"] == {"ticker": "MSFT", "inserted": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_upload_market_data_csv_splits_multiple_tickers_in_one_stooq_file(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.upload_csv_task)
    txt_bytes = (
        b"AAPL.US,D,20260604,000000,313.23,313.54,309.65,311.23,44869134,0\n"
        b"MSFT.US,D,20260604,000000,450.0,451.0,448.0,449.5,30000000,0\n"
    )
    upload = UploadFile(file=io.BytesIO(txt_bytes), filename="basket.txt")

    async with session_factory() as db:
        response = await data_router.upload_market_data_csv(
            ticker=None, file=upload, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert {r["ticker"] for r in job["result"]} == {"AAPL", "MSFT"}
    assert all(r["inserted"] == 1 for r in job["result"])


# ---- POST /api/data/import/{ticker} (Alpha Vantage) ------------------------

class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_import_market_data_from_web_inserts_bars(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)
    payload = {
        "Time Series (Daily)": {
            "2024-01-03": {"1. open": "186.0", "2. high": "187.0", "3. low": "185.5", "4. close": "186.5", "5. volume": "900000"},
            "2024-01-02": {"1. open": "185.5", "2. high": "186.0", "3. low": "184.75", "4. close": "185.9", "5. volume": "1000000"},
        }
    }

    captured = {}

    async def fake_get(self, url, params=None, **kwargs):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "aapl", start_date=None, end_date=None, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert job["result"] == {"ticker": "AAPL", "inserted": 2, "skipped": 0}
    assert captured["params"]["symbol"] == "aapl"
    assert captured["params"]["function"] == "TIME_SERIES_DAILY"


@pytest.mark.asyncio
async def test_import_market_data_from_web_records_importer(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)
    payload = {
        "Time Series (Daily)": {
            "2024-01-02": {"1. open": "185.5", "2. high": "186.0", "3. low": "184.75", "4. close": "185.9", "5. volume": "1000000"},
        }
    }

    async def fake_get(self, url, params=None, **kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "aapl", start_date=None, end_date=None, db=db, current_user=user,
        )
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)
    assert job["status"] == "success"

    async with session_factory() as db:
        row = (await db.execute(select(MarketData).where(MarketData.ticker == "AAPL"))).scalars().first()
    assert row.imported_by == user.id


@pytest.mark.asyncio
async def test_import_market_data_from_web_filters_to_date_window(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)
    payload = {
        "Time Series (Daily)": {
            "2024-01-01": {"1. open": "1", "2. high": "1", "3. low": "1", "4. close": "1", "5. volume": "1"},
            "2024-01-02": {"1. open": "2", "2. high": "2", "3. low": "2", "4. close": "2", "5. volume": "2"},
            "2024-01-05": {"1. open": "3", "2. high": "3", "3. low": "3", "4. close": "3", "5. volume": "3"},
        }
    }

    async def fake_get(self, url, params=None, **kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "aapl", start_date=datetime(2024, 1, 2), end_date=datetime(2024, 1, 2), db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "success"
    assert job["result"] == {"ticker": "AAPL", "inserted": 1, "skipped": 0}


@pytest.mark.asyncio
async def test_import_market_data_from_web_404s_on_invalid_symbol(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)
    payload = {"Error Message": "Invalid API call. Please retry or visit the documentation."}

    async def fake_get(self, url, params=None, **kwargs):
        return _FakeResponse(payload)

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "NOTATICKER", start_date=None, end_date=None, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "failed"
    assert "Invalid API call" in job["error_message"]


@pytest.mark.asyncio
async def test_import_market_data_from_web_400s_when_key_is_unset_or_limited(session_factory, user, monkeypatch):
    """Demo-key / rate-limit responses come back as 200 with an
    Information/Note field instead of Time Series data — surface that as a
    failed job with Alpha Vantage's own message, not a generic failure."""
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)

    async def fake_get(self, url, params=None, **kwargs):
        return _FakeResponse({"Information": "The **demo** API key is for demo purposes only."})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "AAPL", start_date=None, end_date=None, db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "failed"
    assert "demo" in job["error_message"].lower()


# ---- "Never raises" contract: non-ValueError failures still fail the job --

@pytest.mark.asyncio
async def test_execute_csv_import_marks_job_failed_on_non_value_error(session_factory, user, monkeypatch):
    """execute_csv_import's docstring promises 'Never raises — failure is
    recorded on the job row', matching execute_backtest/execute_portfolio_backtest.
    Before this fix it only caught ValueError around _split_bar_groups/
    _bulk_upsert_market_data, so a non-ValueError (e.g. a DB-level error, or
    any other bug) would propagate out of the Celery task uncaught and leave
    the job stuck at status='running' forever. Force a plain RuntimeError and
    assert it's recorded as a failed job instead."""
    async with session_factory() as db:
        job = DataImportJob(user_id=user.id, source="csv", ticker="AAPL", status="pending")
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(data_import_service, "_split_bar_groups", boom)

    csv_bytes = b"Date,Open,High,Low,Close,Volume\n2024-01-02,1,2,0.5,1.5,100\n"
    async with session_factory() as db:
        await data_import_service.execute_csv_import(job_id, "AAPL", csv_bytes, db)

    async with session_factory() as db:
        result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
        job = result.scalars().first()

    assert job.status == "failed"
    assert "RuntimeError" in job.error_message
    assert "boom" in job.error_message


@pytest.mark.asyncio
async def test_execute_alpha_vantage_import_marks_job_failed_on_non_retryable_http_error(
    session_factory, user, monkeypatch
):
    """Only httpx.ConnectError/httpx.TimeoutException should propagate
    uncaught from execute_alpha_vantage_import — tasks.py's
    import_alpha_vantage_task catches exactly those two to drive its retry
    logic. Every other httpx.HTTPError subtype (e.g. RemoteProtocolError,
    ReadError) is not retried and must be recorded on the job directly, to
    satisfy the same 'never raises' contract as execute_csv_import."""
    async with session_factory() as db:
        job = DataImportJob(user_id=user.id, source="alpha_vantage", ticker="AAPL", status="pending")
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    async def fake_get(self, url, params=None, **kwargs):
        raise httpx.RemoteProtocolError("peer closed connection")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        await data_import_service.execute_alpha_vantage_import(
            job_id, "AAPL", "demo", "compact", None, None, db
        )

    async with session_factory() as db:
        result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
        job = result.scalars().first()

    assert job.status == "failed"
    assert "RemoteProtocolError" in job.error_message


@pytest.mark.asyncio
async def test_execute_alpha_vantage_import_lets_connect_error_propagate(session_factory, user, monkeypatch):
    """ConnectError/TimeoutException must NOT be swallowed here — they need
    to reach tasks.py's import_alpha_vantage_task uncaught so its retry
    logic (see tests/test_celery_tasks.py) can catch them."""
    async with session_factory() as db:
        job = DataImportJob(user_id=user.id, source="alpha_vantage", ticker="AAPL", status="pending")
        db.add(job)
        await db.commit()
        await db.refresh(job)
        job_id = job.id

    async def fake_get(self, url, params=None, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        with pytest.raises(httpx.ConnectError):
            await data_import_service.execute_alpha_vantage_import(
                job_id, "AAPL", "demo", "compact", None, None, db
            )


@pytest.mark.asyncio
async def test_import_market_data_from_web_uses_explicit_api_key_over_env(session_factory, user, monkeypatch):
    _run_task_delay_in_new_thread(monkeypatch, data_router.import_alpha_vantage_task)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "env-key")
    captured = {}

    async def fake_get(self, url, params=None, **kwargs):
        captured["params"] = params
        return _FakeResponse({"Error Message": "nope"})

    monkeypatch.setattr(httpx.AsyncClient, "get", fake_get)

    async with session_factory() as db:
        response = await data_router.import_market_data_from_web(
            "AAPL", start_date=None, end_date=None, api_key="explicit-key", db=db, current_user=user,
        )
        assert response["status"] == "pending"
        job = await data_router.get_import_job(response["job_id"], db=db, current_user=user)

    assert job["status"] == "failed"
    assert captured["params"]["apikey"] == "explicit-key"

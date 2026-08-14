"""Shared market-data import helpers, used by both the synchronous
validation step in routers/data.py and the async Celery tasks in tasks.py
that perform the actual (potentially slow) bulk insert. Kept out of
routers/data.py so tasks.py doesn't need to import a FastAPI router module
just to reach these functions."""
import io
import re
from typing import Any, Dict, Optional

import httpx
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import DataImportJob, MarketData

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

REQUIRED_BAR_COLUMNS = {"date", "open", "high", "low", "close", "volume"}

_STOOQ_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^_-]+\.[A-Za-z]{1,3}$")
_STOOQ_COLUMNS = ["ticker", "period", "date", "time", "open", "high", "low", "close", "volume", "openint"]


def _parse_bar_dates(series: pd.Series) -> pd.Series:
    """Parse a date column that's either a normal date string (left to
    pandas' own inference) or a bare YYYYMMDD value (Excel/Stooq-style
    exports store dates as e.g. 19840907, not "1984-09-07"). Without an
    explicit format, `pd.to_datetime` on a raw 8-digit number reads it as
    nanoseconds-since-epoch instead of a calendar date — 19840907 becomes
    1970-01-01T00:00:00.019840907, not 1984-09-07 — so that shape needs
    `format="%Y%m%d"` applied explicitly."""
    text = series.astype(str).str.strip()
    non_null = text[series.notna()]
    if not non_null.empty and non_null.str.fullmatch(r"\d{8}").all():
        return pd.to_datetime(text, format="%Y%m%d", errors="coerce")
    return pd.to_datetime(series)


def _split_bar_groups(content: bytes, ticker: Optional[str]) -> list[tuple[str, pd.DataFrame]]:
    """Parse an uploaded market-data file into one or more (ticker, DataFrame)
    groups. See routers/data.py's module docstring for the two supported
    shapes (header'd CSV vs. headerless Stooq-style per-symbol export)."""
    text = content.decode("utf-8-sig", errors="replace")
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    first_cell = first_line.split(",")[0].strip()
    is_stooq_header = first_cell.upper() == "<TICKER>"
    is_stooq_data = bool(_STOOQ_SYMBOL_RE.match(first_cell))

    if is_stooq_header or is_stooq_data:
        df = pd.read_csv(
            io.StringIO(text),
            header=0 if is_stooq_header else None,
            names=None if is_stooq_header else _STOOQ_COLUMNS,
        )
        df = df.rename(columns={c: c.strip().lower().strip("<>") for c in df.columns})
        df = df.rename(columns={"per": "period", "vol": "volume"})
        missing = {"ticker", "date", "open", "high", "low", "close", "volume"} - set(df.columns)
        if missing:
            raise ValueError(f"Data is missing required column(s): {', '.join(sorted(missing))}")
        df["ticker"] = df["ticker"].astype(str).str.split(".").str[0].str.upper()
        return [(symbol, group.drop(columns=["ticker"])) for symbol, group in df.groupby("ticker", sort=True)]

    try:
        df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        raise ValueError(f"Could not parse file: {e}")
    if not ticker:
        raise ValueError("ticker is required for this file format")
    return [(ticker, df)]


async def _bulk_upsert_market_data(ticker: str, df: pd.DataFrame, db: AsyncSession) -> dict:
    """Insert bars from `df` for `ticker`, skipping any (ticker, date) pair
    already stored."""
    df = df.rename(columns={c: c.strip().lower().replace(" ", "_") for c in df.columns})
    missing = REQUIRED_BAR_COLUMNS - set(df.columns)
    if missing:
        raise ValueError(f"Data is missing required column(s): {', '.join(sorted(missing))}")

    df["date"] = _parse_bar_dates(df["date"])
    df = df.dropna(subset=["date", "open", "high", "low", "close", "volume"])
    if df.empty:
        return {"ticker": ticker, "inserted": 0, "skipped": 0}

    existing_result = await db.execute(
        select(MarketData.date).where(
            MarketData.ticker == ticker,
            MarketData.date.in_(df["date"].tolist()),
        )
    )
    seen_dates = set(existing_result.scalars().all())

    has_adj_close = "adj_close" in df.columns
    inserted = 0
    for rec in df.to_dict("records"):
        row_date = rec["date"]
        if hasattr(row_date, "to_pydatetime"):
            row_date = row_date.to_pydatetime()
        if row_date in seen_dates:
            continue
        db.add(MarketData(
            ticker=ticker,
            date=row_date,
            open=str(rec["open"]),
            high=str(rec["high"]),
            low=str(rec["low"]),
            close=str(rec["close"]),
            volume=str(rec["volume"]),
            adj_close=str(rec["adj_close"]) if has_adj_close and pd.notna(rec.get("adj_close")) else None,
        ))
        seen_dates.add(row_date)
        inserted += 1

    await db.commit()
    return {"ticker": ticker, "inserted": inserted, "skipped": len(df) - inserted}


async def _mark_job_failed(job_id: int, message: str, db: AsyncSession) -> None:
    result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
    job = result.scalars().first()
    if job:
        job.status = "failed"
        job.error_message = message
        await db.commit()


async def execute_csv_import(job_id: int, ticker: Optional[str], content: bytes, db: AsyncSession) -> None:
    """Run the actual (potentially slow) bulk insert for an already-created
    pending DataImportJob row, re-parsing the same raw file bytes the router
    already validated once synchronously. Runs inside a Celery worker via
    asyncio.run(). Never raises — failure is recorded on the job row."""
    result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
    job = result.scalars().first()
    if job is None:
        return
    job.status = "running"
    await db.commit()

    try:
        groups = _split_bar_groups(content, ticker)
        results = []
        for group_ticker, group_df in groups:
            results.append(await _bulk_upsert_market_data(group_ticker.upper(), group_df, db))
    except Exception as e:
        await db.rollback()
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        await db.commit()
        return

    job.result = results[0] if len(results) == 1 else results
    job.status = "success"
    await db.commit()


async def execute_alpha_vantage_import(
    job_id: int, ticker: str, api_key: str, outputsize: str,
    start_date: Optional[str], end_date: Optional[str], db: AsyncSession,
) -> None:
    """Fetch and bulk-insert a ticker's Alpha Vantage daily history for an
    already-created pending DataImportJob row. Runs inside a Celery worker
    via asyncio.run(). Lets httpx.ConnectError/httpx.TimeoutException
    propagate uncaught so the calling task (tasks.py) can retry — every
    other failure mode is recorded on the job row directly."""
    result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
    job = result.scalars().first()
    if job is None:
        return
    job.status = "running"
    await db.commit()

    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "outputsize": outputsize,
        "apikey": api_key,
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.get(ALPHA_VANTAGE_URL, params=params)
    except (httpx.ConnectError, httpx.TimeoutException):
        # Let these propagate uncaught — tasks.py's import_alpha_vantage_task
        # catches them specifically to retry. Every other httpx.HTTPError
        # subtype (RemoteProtocolError, ReadError, etc.) is not retried and
        # is recorded on the job directly, below.
        raise
    except Exception as e:
        await db.rollback()
        job.status = "failed"
        job.error_message = f"{type(e).__name__}: {e}"
        await db.commit()
        return

    try:
        payload = response.json()
    except ValueError as e:
        job.status = "failed"
        job.error_message = f"Could not parse data provider response: {e}"
        await db.commit()
        return

    time_series = payload.get("Time Series (Daily)")
    if not time_series:
        job.status = "failed"
        job.error_message = (
            payload.get("Error Message")
            or payload.get("Note")
            or payload.get("Information")
            or f"No data found for ticker '{ticker}'"
        )
        await db.commit()
        return

    df = pd.DataFrame([
        {
            "date": date_str,
            "open": values["1. open"],
            "high": values["2. high"],
            "low": values["3. low"],
            "close": values["4. close"],
            "volume": values["5. volume"],
        }
        for date_str, values in time_series.items()
    ])
    df["date"] = pd.to_datetime(df["date"])
    if start_date is not None:
        df = df[df["date"] >= pd.to_datetime(start_date)]
    if end_date is not None:
        df = df[df["date"] <= pd.to_datetime(end_date)]

    try:
        job_result = await _bulk_upsert_market_data(ticker.upper(), df, db)
    except ValueError as e:
        job.status = "failed"
        job.error_message = str(e)
        await db.commit()
        return

    job.result = job_result
    job.status = "success"
    await db.commit()

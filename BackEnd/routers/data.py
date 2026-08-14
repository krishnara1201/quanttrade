import io
import os
import re
from datetime import datetime
from typing import Optional

import httpx
import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project, Strategy, MarketData
from database.connection import AsyncSessionLocal, get_db
from services.auth_service import get_current_user

router = APIRouter(prefix="/api/data", tags=["data"])

# Alpha Vantage's documented free-tier REST API (https://www.alphavantage.co/support/#api-key)
# for daily OHLCV history. A free key takes under a minute to claim and needs
# no card. Falls back to the public "demo" key, which Alpha Vantage restricts
# to a handful of sample symbols (e.g. IBM) — real use requires a real key,
# either set server-side via ALPHA_VANTAGE_API_KEY or passed per-request.
#
# Earlier this hit Stooq's CSV export instead, which is unauthenticated and
# needs no key — but Stooq now gates that endpoint behind a JS proof-of-work
# challenge aimed at blocking exactly this kind of scripted request, so it no
# longer returns data to a server-side client. Solving that challenge would
# mean building bot-detection evasion, which isn't something to do here;
# Alpha Vantage is the documented, ToS-compliant replacement.
ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"

REQUIRED_BAR_COLUMNS = {"date", "open", "high", "low", "close", "volume"}

# Stooq's per-symbol export format: one row per bar, no ownership/auth
# concerns since it's the same public OHLCV shape as the CSV path, just
# headerless with the ticker embedded in column 0 as `SYMBOL.COUNTRY` (e.g.
# `AAPL.US`), optionally preceded by a `<TICKER>,<PER>,...` header row.
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
    groups. Two shapes are supported:
      - a header'd OHLCV CSV (Date/Open/High/Low/Close/Volume, e.g. a Yahoo
        Finance/broker export) — the long-standing format; `ticker` must be
        supplied by the caller since the file itself doesn't carry one.
      - a Stooq-style per-symbol export, headerless or with a `<TICKER>,...`
        header, one row per bar as `TICKER.COUNTRY,PERIOD,YYYYMMDD,HHMMSS,
        OPEN,HIGH,LOW,CLOSE,VOLUME,OPENINT` — the ticker is read off each row
        instead (country suffix stripped), so `ticker` is optional and a file
        spanning multiple symbols is split into one group per symbol.
    """
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
    already stored — shared by the CSV-upload and web-import endpoints so both
    behave the same way around duplicates and column parsing."""
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

class MarketDataCreate(BaseModel):
    ticker: str
    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    adj_close: Optional[float] = None

    class Config:
        extra = "forbid"  # reject unexpected fields like created_at sent as string

@router.post("/upload")
async def upload_market_data(data: MarketDataCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_data = MarketData(
        ticker=data.ticker,
        date=data.date,
        open=str(data.open),
        high=str(data.high),
        low=str(data.low),
        close=str(data.close),
        volume=str(data.volume),
        adj_close=str(data.adj_close) if data.adj_close is not None else None,
    )
    db.add(db_data)
    await db.commit()
    await db.refresh(db_data)
    return db_data

@router.post("/upload-csv")
async def upload_market_data_csv(
    ticker: Optional[str] = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Bulk-import OHLCV history from an uploaded file — either a header'd CSV
    (Date/Open/High/Low/Close/Volume, e.g. a Yahoo Finance/broker export;
    `ticker` is required for this shape) or a Stooq-style per-symbol export
    (headerless `TICKER.COUNTRY,PERIOD,YYYYMMDD,HHMMSS,O,H,L,C,V,OpenInt` rows),
    which carries its own ticker per row — `ticker` is optional there, and a
    file spanning multiple symbols is imported as one group per symbol. See
    `_split_bar_groups` for the format-detection details."""
    content = await file.read()
    ticker = ticker.strip().upper() if ticker else None

    try:
        groups = _split_bar_groups(content, ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    results = []
    for group_ticker, group_df in groups:
        try:
            results.append(await _bulk_upsert_market_data(group_ticker.upper(), group_df, db))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    return results[0] if len(results) == 1 else results

@router.post("/import/{ticker}")
async def import_market_data_from_web(
    ticker: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    api_key: Optional[str] = None,
    outputsize: str = "compact",
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch a ticker's daily OHLCV history from Alpha Vantage and bulk-import
    it, so a user can populate data by ticker symbol alone instead of sourcing
    a CSV themselves. Needs a free Alpha Vantage API key (no card required) —
    pass one via `api_key`, or set ALPHA_VANTAGE_API_KEY server-side; without
    either, only Alpha Vantage's demo symbols (e.g. IBM) will work.

    Defaults to `outputsize=compact` (last ~100 daily bars) since Alpha
    Vantage now gates `outputsize=full` behind a paid plan even for
    TIME_SERIES_DAILY — pass `outputsize=full` explicitly if your key has
    that entitlement."""
    key = api_key or os.getenv("ALPHA_VANTAGE_API_KEY", "demo")
    params = {
        "function": "TIME_SERIES_DAILY",
        "symbol": ticker,
        "outputsize": outputsize,
        "apikey": key,
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as http_client:
            response = await http_client.get(ALPHA_VANTAGE_URL, params=params)
        payload = response.json()
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Could not reach data provider: {e}")
    except ValueError as e:
        raise HTTPException(status_code=502, detail=f"Could not parse data provider response: {e}")

    time_series = payload.get("Time Series (Daily)")
    if not time_series:
        detail = (
            payload.get("Error Message")
            or payload.get("Note")
            or payload.get("Information")
            or f"No data found for ticker '{ticker}'"
        )
        status_code = 404 if "Error Message" in payload else 400
        raise HTTPException(status_code=status_code, detail=detail)

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
        df = df[df["date"] >= start_date]
    if end_date is not None:
        df = df[df["date"] <= end_date]

    try:
        return await _bulk_upsert_market_data(ticker.upper(), df, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.get("/tickers")
async def get_tickers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MarketData.ticker).distinct().order_by(MarketData.ticker))
    tickers = result.scalars().all()
    return tickers

@router.get("/{ticker}/range")
async def get_ticker_range(ticker: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Earliest/latest date and row count available for a ticker, so a caller
    (e.g. a backtest date picker) can constrain its selection to a range that
    actually has data before submitting a backtest run."""
    result = await db.execute(
        select(
            func.min(MarketData.date),
            func.max(MarketData.date),
            func.count(MarketData.id),
        ).where(MarketData.ticker == ticker)
    )
    start_date, end_date, count = result.one()
    if not count:
        raise HTTPException(status_code=404, detail=f"No market data found for ticker '{ticker}'")
    return {"ticker": ticker, "start_date": start_date, "end_date": end_date, "count": count}

@router.get("/{ticker}/historical")
async def get_historical_data(
    ticker: str,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Historical bars for a ticker, optionally windowed to [start_date, end_date]
    (inclusive) and always chronologically ordered, matching the same query
    services/backtest_service.py issues when it loads data for a backtest run."""
    query = select(MarketData).where(MarketData.ticker == ticker)
    if start_date is not None:
        query = query.where(MarketData.date >= start_date)
    if end_date is not None:
        query = query.where(MarketData.date <= end_date)
    query = query.order_by(MarketData.date)
    result = await db.execute(query)
    data = result.scalars().all()
    return data

@router.delete("/{data_id}")
async def delete_market_data(data_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(MarketData).where(MarketData.id == data_id)
    )
    data = result.scalars().first()
    if data is None:
        return {"error": "Market data not found"}
    await db.delete(data)
    await db.commit()
    return {"detail": "Market data deleted"}
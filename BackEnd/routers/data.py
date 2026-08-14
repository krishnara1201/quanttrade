import base64
import os
from datetime import datetime
from typing import Optional

import httpx
import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project, Strategy, MarketData, DataImportJob
from database.connection import AsyncSessionLocal, get_db
from services.auth_service import get_current_user
from services.data_import_service import ALPHA_VANTAGE_URL, _split_bar_groups
from tasks import import_alpha_vantage_task, upload_csv_task

router = APIRouter(prefix="/api/data", tags=["data"])


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
    `_split_bar_groups` for the format-detection details.

    Validates the uploaded file's shape synchronously (so a malformed file
    or a missing required ticker still 400s immediately), then hands the
    actual bulk insert to a Celery task — see GET /jobs/{job_id} to poll."""
    content = await file.read()
    ticker = ticker.strip().upper() if ticker else None

    try:
        groups = _split_bar_groups(content, ticker)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    job = DataImportJob(
        user_id=current_user.id,
        source="csv",
        ticker=groups[0][0] if len(groups) == 1 else None,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    upload_csv_task.delay(job.id, ticker, base64.b64encode(content).decode("ascii"))
    return {"job_id": job.id, "status": job.status}

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
    """Enqueue an Alpha Vantage fetch + bulk import — so a user can populate
    data by ticker symbol alone instead of sourcing a CSV themselves. Needs a
    free Alpha Vantage API key (no card required) — pass one via `api_key`,
    or set ALPHA_VANTAGE_API_KEY server-side; without either, only Alpha
    Vantage's demo symbols (e.g. IBM) will work.

    Defaults to `outputsize=compact` (last ~100 daily bars) since Alpha
    Vantage now gates `outputsize=full` behind a paid plan even for
    TIME_SERIES_DAILY — pass `outputsize=full` explicitly if your key has
    that entitlement. See GET /jobs/{job_id} to poll."""
    key = api_key or os.getenv("ALPHA_VANTAGE_API_KEY", "demo")

    job = DataImportJob(user_id=current_user.id, source="alpha_vantage", ticker=ticker.upper(), status="pending")
    db.add(job)
    await db.commit()
    await db.refresh(job)

    import_alpha_vantage_task.delay(
        job.id, ticker, key, outputsize,
        start_date.isoformat() if start_date else None,
        end_date.isoformat() if end_date else None,
    )
    return {"job_id": job.id, "status": job.status}

@router.get("/jobs/{job_id}")
async def get_import_job(job_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(DataImportJob).where(DataImportJob.id == job_id))
    job = result.scalars().first()
    if job is None:
        raise HTTPException(status_code=404, detail="Import job not found")
    if job.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")
    return {
        "job_id": job.id,
        "source": job.source,
        "ticker": job.ticker,
        "status": job.status,
        "result": job.result,
        "error_message": job.error_message,
        "created_at": job.created_at.isoformat(),
    }

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
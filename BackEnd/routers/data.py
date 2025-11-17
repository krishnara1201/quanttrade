from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project, Strategy, MarketData
from database.connection import AsyncSessionLocal, get_db
from services.auth_service import get_current_user

router = APIRouter(prefix="/api/data", tags=["data"])

class MarketDataCreate(BaseModel):
    symbol: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    
    class Config:
        extra = "forbid"  # reject unexpected fields like created_at sent as string
        
@router.post("/upload")
async def upload_market_data(data: MarketDataCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    db_data = MarketData(**data.dict())
    db.add(db_data)
    await db.commit()
    await db.refresh(db_data)
    return db_data

@router.get("/tickers")
async def get_tickers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(select(MarketData.ticker).distinct())
    tickers = result.scalars().all()
    return tickers

@router.get("/{ticker}/historical")
async def get_historical_data(ticker: str, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(MarketData).where(MarketData.ticker == ticker)
    )
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
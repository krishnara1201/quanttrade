from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db
from database.models import User, Project, Strategy, MarketData, BacktestResult
from jose import JWTError, jwt as jose_jwt
from services.auth_service import get_current_user

async def create_data_from_ticker(ticker: str, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    # Implementation for creating data
    data = await db.execute(select(MarketData).where(MarketData.ticker == ticker))
    df = data.scalars().all().to_dataframe()
    if df:
        return df
    else:
        raise HTTPException(status_code=404, detail="Market data not found")

async def get_data_in_date_range(ticker: str, start_date: str, end_date: str, db: AsyncSession = Depends(get_db),
                    user: User = Depends(get_current_user)):
    # Implementation for retrieving data in date range
    result = await db.execute(
        select(MarketData).where(
            MarketData.ticker == ticker,
            MarketData.date >= start_date,
            MarketData.date <= end_date
        )
    )
    data = result.scalars().all().to_dataframe()
    if data:
        return data
    else:
        raise HTTPException(status_code=404, detail="No market data found in the specified date range")
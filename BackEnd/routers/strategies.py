from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from BackEnd.database.models import User, Project, Strategy
from BackEnd.database.connection import AsyncSessionLocal, get_db
from BackEnd.services.auth_service import get_current_user

router = APIRouter(prefix="/strategies", tags=["strategies"])

router.get("/")
async def read_strategies(db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(select(Strategy))
    strategies = result.scalars().all()
    return strategies

router.get("/{strategy_id}")
async def read_strategy(strategy_id: int, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Strategy).where(Strategy.id == strategy_id)
    )
    strategy = result.scalars().first()
    if strategy is None:
        return {"error": "Strategy not found"}
    return strategy

router.post("/")
async def create_strategy(strategy_data: dict, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
        db_strategy = Strategy(**strategy_data)
        db.add(db_strategy)
        await db.commit()
        await db.refresh(db_strategy)
        return db_strategy
    
router.put("/{strategy_id}")
async def update_strategy(strategy_id: int, strategy_data: dict, db: AsyncSession = Depends(get_db),
                            user: User = Depends(get_current_user)):
        result = await db.execute(
            select(Strategy).where(Strategy.id == strategy_id)
        )
        strategy = result.scalars().first()
        if strategy is None:
            return {"error": "Strategy not found"}
        for key, value in strategy_data.items():
            setattr(strategy, key, value)
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)
        return strategy
    

import bcrypt
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from BackEnd.database.models import User, Project
from BackEnd.database.connection import AsyncSessionLocal, get_db
from BackEnd.services.auth_service import get_current_user
from passlib.context import CryptContext
from datetime import datetime

router = APIRouter(prefix="/users", tags=["users"])

class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}

class ProjectOut(BaseModel):
    id: int
    name: str
    owner_id: int
    description: Optional[str] = None
    created_at: Optional[datetime] = None
    model_config = {"from_attributes": True}

@router.get("/{user_id}")
async def read_user(user_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalars().first()
    if user is None:
        return {"error": "User not found"}
    return UserOut.from_orm(user)

@router.get("/{user_id}/projects/")
async def read_user_projects(user_id: int, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Project).where(Project.owner_id == user_id)
    )
    projects = result.scalars().all()
    return [ProjectOut.from_orm(p) for p in projects]

@router.get("/me")
async def read_current_user(db: AsyncSession = Depends(get_db),
                            current_user: User = Depends(get_current_user)):
    return UserOut.from_orm(current_user)

@router.get("/me/projects/")
async def read_my_projects(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    result = await db.execute(
        select(Project).where(Project.owner_id == current_user.id)
    )
    projects = result.scalars().all()
    return [ProjectOut.from_orm(p) for p in projects]


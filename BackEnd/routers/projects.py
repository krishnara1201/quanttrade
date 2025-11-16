from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from BackEnd.database.models import User, Project
from BackEnd.database.connection import AsyncSessionLocal, get_db
from BackEnd.services.auth_service import get_current_user

router = APIRouter(prefix="/projects", tags=["projects"])

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    owner_id: int
    strategy: str
    
    class Config:
        extra = "forbid"  # reject unexpected fields like created_at sent as string
        
@router.get("/")
async def read_projects(db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(select(Project))
    projects = result.scalars().all()
    return projects

@router.post("/")
async def create_project(project_data: ProjectCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    db_project = Project(**project_data)
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project
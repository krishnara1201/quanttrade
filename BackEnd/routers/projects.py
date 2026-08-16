from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project, Strategy
from database.connection import AsyncSessionLocal, get_db
from services.auth_service import get_current_user

router = APIRouter(prefix="/api/projects", tags=["projects"])

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    
    class Config:
        extra = "forbid"  # reject unexpected fields like created_at sent as string
        
@router.get("/")
async def read_projects(db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    result = await db.execute(select(Project).where(Project.owner_id == user.id))
    projects = result.scalars().all()
    return projects

@router.post("/")
async def create_project(project_data: ProjectCreate, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    db_project = Project(**project_data.dict(), owner_id=user.id)
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project

@router.delete("/{project_id}")
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalars().first()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    if project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    await db.delete(project)
    await db.commit()
    return {"detail": "Project deleted"}
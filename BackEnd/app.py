from sqlalchemy import select
from fastapi import FastAPI, Depends
from dotenv import load_dotenv
from database.models import Project, User
from database.connection import engine, Base, init_db, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession

load_dotenv()

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    # Create database tables (if they don't exist)
    await init_db()
        

@app.on_event("shutdown")
async def on_shutdown():
    # Cleanup resources, close connections if needed
    await engine.dispose()  # Dispose async SQLAlchemy engine
    

async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

@app.get("/")
async def read_root():
    return {"message": "Hello World"}

       
@app.post("/users/")
async def create_user(user_data: dict, db: AsyncSession = Depends(get_db)):
    db_user = User(**user_data)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    return db_user
    
@app.get("/users/{user_id}")
async def read_user(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.id == user_id)
    )
    user = result.scalars().first()
    if user is None:
        return {"error": "User not found"}
    return user

@app.get("/users/{user_id}/projects/")
async def read_user_projects(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Project).where(Project.owner_id == user_id)
    )
    projects = result.scalars().all()
    return projects

@app.get("/projects/")
async def read_projects(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Project))
    projects = result.scalars().all()
    return projects

@app.post("/projects/")
async def create_project(project_data, db: AsyncSession = Depends(get_db)):
    db_project = Project(**project_data)
    db.add(db_project)
    await db.commit()
    await db.refresh(db_project)
    return db_project


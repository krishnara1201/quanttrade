from sqlalchemy import select
from fastapi import FastAPI, Depends
from dotenv import load_dotenv
from BackEnd.database.models import Project, User
from BackEnd.database.connection import engine, Base, init_db, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from BackEnd.routers import users, projects, auth

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
  

@app.get("/")
async def read_root():
    return {"message": "Hello World"}


app.include_router(users.router)
app.include_router(projects.router)
app.include_router(auth.router)


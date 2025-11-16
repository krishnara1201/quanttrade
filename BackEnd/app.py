from sqlalchemy import select
from fastapi import FastAPI, Depends
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from database.models import Project, User
from database.connection import engine, Base, init_db, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from routers import users, projects, auth, strategies, data
from services.rate_limiter import fixed_window
import threading
load_dotenv()

app = FastAPI()
ip_cache = {}
lock = threading.Lock()


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
app.include_router(strategies.router)
app.include_router(data.router)

@app.middleware("http")
async def db_session_middleware(request, call_next):
    response = None
    async with AsyncSessionLocal() as session:
        request.state.db = session
        response = await call_next(request)
    return response

@app.middleware("http")
async def rate_limit_middleware(request, call_next):
    # Placeholder for rate limiting logic
    ip_address = request.client.host

    with lock:
        if ip_address not in ip_cache:
            ip_cache[ip_address] = fixed_window(60, 100)  # 100 requests per 60 seconds
        limiter = ip_cache[ip_address]

    if not limiter.is_allowed(request.url.path):
        return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})
    else:
        limiter.increment()
        response = await call_next(request)
        return response




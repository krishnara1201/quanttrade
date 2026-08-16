from pathlib import Path

from sqlalchemy import select
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
from database.models import Project, User
from database.connection import engine, AsyncSessionLocal
from sqlalchemy.ext.asyncio import AsyncSession
from routers import users, projects, auth, strategies, data, backtest
from services.rate_limiter import fixed_window
from services.auth_service import validate_secret_key
import threading
import os
load_dotenv()
validate_secret_key()

app = FastAPI()

# Only present in the merged Render deploy image (see deploy/Dockerfile),
# which bakes the built frontend in here so FastAPI can serve it same-origin
# -- absent in local dev/CI, where every route below that depends on it is
# skipped entirely rather than erroring on a missing directory.
STATIC_DIR = Path(__file__).resolve().parent / "static"

# CORS configuration
cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
ip_cache = {}
lock = threading.Lock()


@app.on_event("shutdown")
async def on_shutdown():
    # Cleanup resources, close connections if needed
    await engine.dispose()  # Dispose async SQLAlchemy engine

@app.get("/")
async def read_root():
    if STATIC_DIR.is_dir():
        return FileResponse(STATIC_DIR / "index.html")
    return {"message": "Hello World"}


app.include_router(users.router)
app.include_router(projects.router)
app.include_router(auth.router)
app.include_router(strategies.router)
app.include_router(data.router)
app.include_router(backtest.router)

if STATIC_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="frontend-assets")

    # SPA fallback for React Router client-side routes (e.g. /projects,
    # /strategies/5/backtest) -- registered last so every API router above
    # still matches first (including strategies.router's own /strategies/{id},
    # a 2-segment path that can't match the frontend's 3-segment
    # /strategies/:id/backtest route -- that one correctly falls through to
    # here). Bad /api/* paths still 404 as JSON instead of silently serving
    # index.html for a typo'd endpoint.
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str):
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        if full_path == "strategies":
            # Our catch-all structurally matches this bare path too, which
            # would otherwise shadow FastAPI's own redirect_slashes handling
            # for strategies.router's /strategies/ route (see CLAUDE.md's
            # documented router-prefix quirk) -- reproduce it explicitly.
            return RedirectResponse(url="/strategies/", status_code=307)
        # Vite copies FrontEnd/public/* (favicon*, apple-touch-icon.png,
        # icon-512.png, ...) straight into dist/'s root, not under
        # dist/assets/ -- the /assets mount above doesn't cover them, so
        # without this check they'd fall through to the SPA shell below and
        # e.g. the favicon would silently 200 with index.html's HTML instead
        # of image bytes.
        candidate = STATIC_DIR / full_path
        if candidate.is_file() and candidate.resolve().parent == STATIC_DIR:
            return FileResponse(candidate)
        return FileResponse(STATIC_DIR / "index.html")

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




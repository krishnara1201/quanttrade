# Docker containerization — design

## Goal

Let a new contributor run the full stack (Postgres + FastAPI backend + React frontend) with a single `docker compose up`, without needing a local Python/Node/Postgres install. Keep the existing non-Docker manual setup working unchanged for anyone who prefers it.

## Context

- `BackEnd/database/connection.py` currently hardcodes `DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"`. `localhost` inside a container does not reach a sibling Postgres container, so this must become configurable for Docker to work at all.
- `BackEnd/.env.example` already lists a commented-out `DATABASE_URL`, suggesting this was intended but never wired up.
- `app.py` calls `load_dotenv()` after importing `database.connection`, so `connection.py` must call `load_dotenv()` itself to reliably pick up `.env` values regardless of import order.
- Frontend and backend both run in **dev mode** (hot reload) inside containers, matching the project's current active-development stage and its documented local dev commands (`uvicorn --reload`, `npm run dev`).
- `VITE_API_BASE` defaults to `http://localhost:8000` in `FrontEnd/src/api/client.js` already — the browser (not the frontend container) talks directly to the backend's host-published port, so no cross-container networking is needed for that call path.

## Changes

### 1. `BackEnd/database/connection.py`
- Call `load_dotenv()` (the import already exists, unused).
- Change `DATABASE_URL` to `os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/quanttrade")`, preserving current behavior when the env var is unset.

### 2. `BackEnd/Dockerfile`
- Base: `python:3.11-slim`.
- `pip install -r requirements.txt`, copy source.
- `CMD uvicorn app:app --host 0.0.0.0 --port 8000 --reload`.
- Source is bind-mounted at runtime (not baked in) so `--reload` picks up host edits.

### 3. `FrontEnd/Dockerfile`
- Base: `node:20-alpine`.
- `npm install`, copy source.
- `CMD npm run dev -- --host 0.0.0.0`.
- Source bind-mounted at runtime for hot reload.

### 4. `docker-compose.yml` (repo root)
Three services:

- **`postgres`**: `postgres:16-alpine`; env `POSTGRES_USER=postgres`, `POSTGRES_PASSWORD=postgres`, `POSTGRES_DB=quanttrade` (matches the existing hardcoded default); named volume for data persistence; healthcheck via `pg_isready`; publishes `5432` (optional local psql access).
- **`backend`**: builds `BackEnd/Dockerfile`; `env_file: BackEnd/.env` (for `SECRET_KEY`/`ALGORITHM`/`CORS_ORIGINS`); `environment: DATABASE_URL=postgresql+asyncpg://postgres:postgres@postgres:5432/quanttrade` (overrides the env file, points at the compose service name `postgres`); `depends_on: postgres` with `condition: service_healthy`; bind-mounts `./BackEnd:/app`; publishes `8000`.
- **`frontend`**: builds `FrontEnd/Dockerfile`; `environment: VITE_API_BASE=http://localhost:8000`; bind-mounts `./FrontEnd:/app` plus an anonymous volume on `/app/node_modules` (prevents the host bind mount from shadowing the container-installed, platform-correct `node_modules`); `depends_on: backend`; publishes `5173`.

### 5. `.dockerignore`
- `BackEnd/.dockerignore`: `.venv`, `__pycache__`, `.pytest_cache`, `.env`, `*.pyc`.
- `FrontEnd/.dockerignore`: `node_modules`, `dist`, `.env`.

### 6. `BackEnd/.env.example`
- Uncomment/rewrite the `DATABASE_URL` line as a live example, since it's now actually read (still fine to leave unset for local non-Docker dev, which falls back to the hardcoded default).

### 7. Documentation
- `README.md`: add a "Docker Setup" section presenting `cp BackEnd/.env.example BackEnd/.env` → `docker compose up` as the recommended quick start. Keep the existing manual (non-Docker) setup section as-is, as an alternative.
- `CLAUDE.md`: update the paragraph in the Environment section that currently states `DATABASE_URL` is hardcoded and unused — that will no longer be true after change #1.

## Out of scope

- Production/nginx-based frontend build (dev-mode-only per user decision).
- CI/CD, image publishing/registry, Kubernetes manifests.
- Migration tooling (still `Base.metadata.create_all` via `init_db()`, unchanged).
- Removing the unused `psycopg2`/`RealDictCursor` imports in `connection.py` (pre-existing dead code, unrelated to this change).

## Testing

- `docker compose up` brings up all three services; backend becomes healthy only after Postgres passes its healthcheck.
- Backend reachable at `http://localhost:8000`, frontend at `http://localhost:5173`, and the frontend can successfully call the backend (register/login) through the browser.
- Existing `pytest tests/ -v` (non-Docker, using the in-memory sqlite fixtures) continues to pass unaffected, since those tests don't go through `connection.py`'s `DATABASE_URL`.
- Editing a backend or frontend source file on the host and seeing the running container reload confirms the bind-mount/hot-reload setup works.

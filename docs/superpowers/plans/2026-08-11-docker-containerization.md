# Docker Containerization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let `docker compose up` bring up Postgres + the FastAPI backend + the Vite frontend together, with hot reload, so a new contributor can run the full stack without installing Python/Node/Postgres locally.

**Architecture:** Three docker-compose services (`postgres`, `backend`, `frontend`) on the default compose network, communicating by service name. Backend and frontend run in dev mode (`--reload` / `npm run dev`) with source bind-mounted from the host. The one code change required is making `DATABASE_URL` in `BackEnd/database/connection.py` read from the environment (falling back to the current hardcoded value) so the backend container can reach the `postgres` service instead of `localhost`.

**Tech Stack:** Docker, Docker Compose, `postgres:16-alpine`, `python:3.11-slim`, `node:20-alpine`.

## Global Constraints

- Preserve current non-Docker behavior exactly when `DATABASE_URL` is unset (spec: "preserving current behavior when the env var is unset").
- Dev mode only for both containers — no nginx/prod build (spec: "Out of scope: Production/nginx-based frontend build").
- `POSTGRES_USER=postgres`, `POSTGRES_PASSWORD=postgres`, `POSTGRES_DB=quanttrade` (must match the existing hardcoded default in `connection.py`).
- Backend container's `DATABASE_URL` must point at the compose service name `postgres`, not `localhost`.
- `VITE_API_BASE=http://localhost:8000` — the browser calls the backend's host-published port directly; no cross-container call for this path.
- Do not touch: production/CI tooling, migration tooling (`init_db()`/`create_all` unchanged), the unused `psycopg2`/`RealDictCursor` imports in `connection.py`.

---

### Task 1: Make `DATABASE_URL` environment-configurable

**Files:**
- Modify: `BackEnd/database/connection.py`
- Modify: `BackEnd/.env.example`
- Test: `BackEnd/tests/test_connection_config.py`

**Interfaces:**
- Produces: `database.connection.DATABASE_URL` (module-level `str`) — now sourced from `os.getenv("DATABASE_URL", <hardcoded default>)`, called after a local `load_dotenv()`. Later tasks (docker-compose) rely on this env var being honored.

- [ ] **Step 1: Write the failing test**

Create `BackEnd/tests/test_connection_config.py`:

```python
import importlib
import os

import database.connection as connection


def test_database_url_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"


def test_database_url_reads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@postgres:5432/quanttrade")
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://user:pass@postgres:5432/quanttrade"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(connection)
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `BackEnd/`): `pytest tests/test_connection_config.py -v`
Expected: FAIL — `connection.DATABASE_URL` is still the unconditional hardcoded string, so `test_database_url_reads_from_env` fails (env var is ignored).

- [ ] **Step 3: Update `connection.py`**

In `BackEnd/database/connection.py`, replace:

```python
DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"
```

with:

```python
load_dotenv()
DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"
)
```

(`load_dotenv` and `os` are already imported in this file — `load_dotenv` is currently imported but never called.) Place these two lines where the old `DATABASE_URL = ...` line was, still above the `engine = create_async_engine(DATABASE_URL, ...)` line.

- [ ] **Step 4: Run test to verify it passes**

Run (from `BackEnd/`): `pytest tests/test_connection_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the full existing suite to confirm no regression**

Run (from `BackEnd/`): `pytest tests/ -v`
Expected: all tests pass (existing ownership/executor tests are unaffected — they don't import `database.connection`'s `DATABASE_URL` directly).

- [ ] **Step 6: Update `.env.example`**

In `BackEnd/.env.example`, replace:

```
# Database (if using external DB)
# DATABASE_URL=postgresql://user:password@localhost/dbname
```

with:

```
# Database (defaults to postgresql+asyncpg://postgres:postgres@localhost/quanttrade if unset)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost/quanttrade
```

- [ ] **Step 7: Commit**

```bash
git add BackEnd/database/connection.py BackEnd/.env.example BackEnd/tests/test_connection_config.py
git commit -m "$(cat <<'EOF'
Make DATABASE_URL configurable via env var

Prerequisite for Docker: the backend container needs to reach Postgres
by service name instead of localhost. Falls back to the existing
hardcoded value when unset, so non-Docker local dev is unaffected.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Backend Dockerfile

**Files:**
- Create: `BackEnd/Dockerfile`
- Create: `BackEnd/.dockerignore`

**Interfaces:**
- Produces: an image that, when run with source bind-mounted at `/app` and `DATABASE_URL`/`SECRET_KEY`/`ALGORITHM`/`CORS_ORIGINS` set in the environment, serves the FastAPI app on `0.0.0.0:8000` with `--reload`. Consumed by Task 4's `docker-compose.yml`.

- [ ] **Step 1: Create `BackEnd/.dockerignore`**

```
.venv
__pycache__
*.pyc
.pytest_cache
.env
```

- [ ] **Step 2: Create `BackEnd/Dockerfile`**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

- [ ] **Step 3: Build the image to verify it works**

Run (from repo root): `docker build -t quanttrade-backend BackEnd/`
Expected: build completes successfully (exit code 0), ending with the image tagged `quanttrade-backend`.

- [ ] **Step 4: Commit**

```bash
git add BackEnd/Dockerfile BackEnd/.dockerignore
git commit -m "$(cat <<'EOF'
Add backend Dockerfile

Dev-mode image (uvicorn --reload) for the docker-compose stack; source
is bind-mounted at runtime rather than baked in, so edits on the host
trigger reload inside the container.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: Frontend Dockerfile

**Files:**
- Create: `FrontEnd/Dockerfile`
- Create: `FrontEnd/.dockerignore`

**Interfaces:**
- Produces: an image that, when run with source bind-mounted at `/app` (and an anonymous volume protecting `/app/node_modules`), serves the Vite dev server on `0.0.0.0:5173`. Consumed by Task 4's `docker-compose.yml`.

- [ ] **Step 1: Create `FrontEnd/.dockerignore`**

```
node_modules
dist
.env
```

- [ ] **Step 2: Create `FrontEnd/Dockerfile`**

```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
```

- [ ] **Step 3: Build the image to verify it works**

Run (from repo root): `docker build -t quanttrade-frontend FrontEnd/`
Expected: build completes successfully (exit code 0), ending with the image tagged `quanttrade-frontend`.

- [ ] **Step 4: Commit**

```bash
git add FrontEnd/Dockerfile FrontEnd/.dockerignore
git commit -m "$(cat <<'EOF'
Add frontend Dockerfile

Dev-mode image (vite dev server) for the docker-compose stack; source
is bind-mounted at runtime for hot reload.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: docker-compose.yml wiring the three services together

**Files:**
- Create: `docker-compose.yml` (repo root)

**Interfaces:**
- Consumes: `BackEnd/Dockerfile` (Task 2), `FrontEnd/Dockerfile` (Task 3), `BackEnd/database/connection.py`'s `DATABASE_URL` env var support (Task 1), `BackEnd/.env` (user-created from `.env.example`, containing `SECRET_KEY`/`ALGORITHM`/`CORS_ORIGINS`).
- Produces: a running stack reachable at `http://localhost:8000` (backend) and `http://localhost:5173` (frontend), with Postgres data persisted in a named volume `postgres_data`.

- [ ] **Step 1: Create `BackEnd/.env` for local testing (not committed)**

```bash
cp BackEnd/.env.example BackEnd/.env
```

(`.env` is already covered by `.gitignore` per `CLAUDE.md`.)

- [ ] **Step 2: Create `docker-compose.yml`**

```yaml
services:
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
      POSTGRES_DB: quanttrade
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    build: ./BackEnd
    env_file:
      - ./BackEnd/.env
    environment:
      DATABASE_URL: postgresql+asyncpg://postgres:postgres@postgres:5432/quanttrade
    volumes:
      - ./BackEnd:/app
    ports:
      - "8000:8000"
    depends_on:
      postgres:
        condition: service_healthy

  frontend:
    build: ./FrontEnd
    environment:
      VITE_API_BASE: http://localhost:8000
    volumes:
      - ./FrontEnd:/app
      - /app/node_modules
    ports:
      - "5173:5173"
    depends_on:
      - backend

volumes:
  postgres_data:
```

- [ ] **Step 3: Validate the compose file**

Run (from repo root): `docker compose config`
Expected: prints the fully-resolved config with no errors (confirms YAML is valid and interpolation/env_file resolve correctly, given `BackEnd/.env` exists from Step 1).

- [ ] **Step 4: Bring the stack up and smoke-test it**

Run (from repo root): `docker compose up -d --build`
Wait for `backend` to report healthy startup, then:

```bash
curl -sf http://localhost:8000/
curl -sf http://localhost:5173/
```

Expected: the first returns `{"message":"Hello World"}`; the second returns the Vite dev server's HTML (exit code 0, non-empty body). Both confirm the containers are up, Postgres passed its healthcheck (backend wouldn't have started otherwise), and both ports are reachable from the host.

- [ ] **Step 5: Tear down**

```bash
docker compose down
```

- [ ] **Step 6: Commit**

```bash
git add docker-compose.yml
git commit -m "$(cat <<'EOF'
Add docker-compose.yml for full-stack dev environment

Wires postgres + backend + frontend together with hot reload and a
persistent Postgres volume, so `docker compose up` is a complete
local dev setup with no host Python/Node/Postgres install required.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

(`BackEnd/.env` created in Step 1 stays local/untracked — it's gitignored, matching the existing non-Docker workflow.)

---

### Task 5: Update documentation

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

**Interfaces:**
- None (documentation only).

- [ ] **Step 1: Add a Docker section to `README.md`**

In `README.md`, insert a new section directly above `## 🚀 Quick Start` (so Docker is presented as the recommended path, with manual setup as the alternative immediately below it):

```markdown
## 🐳 Docker Setup (Recommended)

Run the full stack — Postgres, backend, and frontend — with Docker Compose. No local Python, Node, or Postgres install needed.

```bash
cp BackEnd/.env.example BackEnd/.env
# Edit BackEnd/.env and replace SECRET_KEY with your generated key (see Security Setup above)
docker compose up
```

- Backend: http://localhost:8000
- Frontend: http://localhost:5173
- Postgres: localhost:5432 (user/password/db: `postgres`/`postgres`/`quanttrade`)

Both backend and frontend run in dev mode with hot reload — edits to files under `BackEnd/` or `FrontEnd/` are picked up automatically. Postgres data persists in a named Docker volume across restarts; `docker compose down -v` removes it.

To rebuild after dependency changes: `docker compose up --build`.
```

- [ ] **Step 2: Update `## 🚀 Quick Start` heading for clarity**

Change:

```markdown
## 📋 Quick Start
```

to (if not already present verbatim, adjust to match the actual existing heading text):

```markdown
## 🚀 Quick Start (Manual, without Docker)
```

- [ ] **Step 3: Update the stale `DATABASE_URL` paragraph in `CLAUDE.md`**

In `CLAUDE.md`, under `### Environment`, replace:

```
Note: the database connection string is currently **hardcoded** in `database/connection.py` (`DATABASE_URL`), not read from env, so `DATABASE_URL` in `.env.example` is currently unused — a local PostgreSQL instance matching that hardcoded URL is required.
```

with:

```
`DATABASE_URL` is read from the environment in `database/connection.py` (via `os.getenv`, falling back to `postgresql+asyncpg://postgres:postgres@localhost/quanttrade` if unset) — set it in `.env` to point at a different Postgres instance. The Docker Compose stack (`docker-compose.yml`) overrides it to point at the `postgres` service.
```

- [ ] **Step 4: Update the `### Backend` and `### Tests` command sections in `CLAUDE.md` to mention Docker as an alternative**

Directly below the existing `### Backend (\`BackEnd/\`)` code block in `CLAUDE.md`, add:

```
Alternatively, run the full stack (Postgres included) via `docker compose up` from the repo root — see `README.md`'s Docker Setup section.
```

- [ ] **Step 5: Review the diff**

```bash
git diff README.md CLAUDE.md
```

Expected: only the additions/edits described above; no unrelated changes.

- [ ] **Step 6: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "$(cat <<'EOF'
Document Docker setup in README and update CLAUDE.md

README gains a Docker Compose quick-start as the recommended path,
with the existing manual setup kept as an alternative. CLAUDE.md's
DATABASE_URL note is corrected now that it's env-configurable.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review Notes

- **Spec coverage:** connection.py env fix → Task 1. Backend Dockerfile → Task 2. Frontend Dockerfile → Task 3. docker-compose.yml (all three services, healthcheck, volumes, env wiring) → Task 4. `.dockerignore` files → Tasks 2/3. `.env.example` update → Task 1. README + CLAUDE.md → Task 5. Testing section of spec (compose up, reachability, hot reload, existing pytest suite unaffected) → covered by Task 1 Step 5 and Task 4 Steps 3-5.
- **Placeholder scan:** no TBD/TODO; all steps have literal file contents or exact commands.
- **Type consistency:** `DATABASE_URL` env var name and format used identically across Task 1 (code), Task 4 (compose `environment:` and `.env.example`), and Task 5 (docs). Service name `postgres` used consistently as the DB host in Task 4's backend `DATABASE_URL` and matches the service block name. Ports `8000`/`5173`/`5432` consistent across all tasks.

# QuantTrade

A full-stack trading strategy backtesting platform with React frontend and FastAPI backend.

## 🚨 Security Setup (IMPORTANT)

### 1. Generate a Strong Secret Key

**NEVER use the example secret key in production!**

Generate a secure key:
```bash
# Python
python -c "import secrets; print(secrets.token_urlsafe(32))"

# Or use OpenSSL
openssl rand -hex 32
```

### 2. Configure Environment Variables

Copy the example file and update with your values:
```bash
cd BackEnd
cp .env.example .env
# Edit .env and replace SECRET_KEY with your generated key
```

### 3. Never Commit .env Files

The `.gitignore` is already configured, but verify:
- ✅ `.env` files are in `.gitignore`
- ✅ Never commit passwords, API keys, or secrets
- ✅ Use `.env.example` as a template only

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

## 🚀 Quick Start (Manual, without Docker)

### Backend Setup
```bash
cd BackEnd
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload
```

### Frontend Setup
```bash
cd FrontEnd
npm install
npm run dev
```

## 📋 Features

- ✅ User authentication (JWT)
- ✅ Project & strategy management
- ✅ Visual strategy builder (no coding required)
- ✅ Backtesting engine with indicators (SMA, RSI, Bollinger Bands)
- ✅ Interactive charts (Recharts)
- ✅ Protected routes & authorization

## 🔒 Security Features

- bcrypt password hashing
- JWT authentication
- CORS protection
- Input validation
- SQL injection prevention (SQLAlchemy ORM)
- No code injection (safe condition parsing)
- Ownership verification on all mutations

## 🛠️ Tech Stack

**Backend:**
- FastAPI
- SQLAlchemy (async)
- PostgreSQL/SQLite
- Pandas for data processing

**Frontend:**
- React 18
- React Router
- Axios
- Recharts
- Vite

## 📝 License

MIT

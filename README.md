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

## 🚀 Quick Start

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

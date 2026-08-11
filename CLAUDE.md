# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

QuantTrade is a full-stack trading strategy backtesting platform: a FastAPI backend (`BackEnd/`) and a React + Vite frontend (`FrontEnd/`). Users create projects, define trading strategies (via indicators + entry/exit rules), and run backtests against stored market data.

## Commands

### Backend (`BackEnd/`)
```bash
cd BackEnd
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload
```

### Tests
```bash
cd BackEnd
pytest tests/ -v
```
Must be run from `BackEnd/` (or with `BackEnd` on `PYTHONPATH`) — `tests/__init__.py` relies on pytest's rootdir-relative import mode to resolve `from services...` imports; running `pytest BackEnd/tests` from the repo root will fail to import. There is no `pytest.ini`/`conftest.py` pinning this.

Test coverage: `services/strategy_executor.py` (`tests/test_strategy_executor.py`) — indicator correctness, position sizing/costs, and metrics, all unit-tested with hand-verified expected values (no mocks, no DB). `tests/test_backtest_ownership.py` and `tests/test_strategy_ownership.py` cover the ownership-check async regression (see below) against a real async session, using an in-memory `sqlite+aiosqlite` engine (dev-only deps `pytest-asyncio`/`aiosqlite`, pinned in `requirements.txt`) rather than mocks — `pytest.ini` sets `asyncio_default_fixture_loop_scope = function`. There is otherwise no test suite for routers/services that touch the database, and no frontend test suite.

### Frontend (`FrontEnd/`)
```bash
cd FrontEnd
npm install
npm run dev       # Vite dev server
npm run build
npm run preview
```
`npm run lint` is a no-op placeholder (`echo "No lint configured yet"`) — no linter is configured.

### Environment
Copy `BackEnd/.env.example` to `BackEnd/.env` and set `SECRET_KEY` (JWT signing) and `ALGORITHM` (e.g. `HS256`); both are read via `os.getenv` in `services/auth_service.py` with no defaults, so the app will error on auth routes if unset. `CORS_ORIGINS` is a comma-separated list consumed in `app.py`. Note: the database connection string is currently **hardcoded** in `database/connection.py` (`DATABASE_URL`), not read from env, so `DATABASE_URL` in `.env.example` is currently unused — a local PostgreSQL instance matching that hardcoded URL is required.

## Architecture

### Backend request flow
`app.py` wires global middleware (in registration order, so the *last*-added middleware runs *first* per Starlette's stack): CORS → per-request DB session middleware (opens an `AsyncSession` and stashes it on `request.state.db`) → a fixed-window rate limiter (in-memory dict keyed by client IP, 100 req/60s, defined in `services/rate_limiter.py`). In practice routers don't use `request.state.db` — they instead resolve a session per-endpoint via the `Depends(get_db)` FastAPI dependency (`database/connection.py`), which is the pattern to follow for new endpoints.

### Router prefixes are inconsistent — check before adding frontend calls
Most routers are mounted under `/api/...` (`auth` → `/api/auth`, `projects` → `/api/projects`, `users` → `/api/users`, `data` → `/api/data`, `backtest` → `/api/backtest`), but `strategies` is mounted at `/strategies` with **no** `/api` prefix (`routers/strategies.py`). The frontend mirrors this exactly (`FrontEnd/src/api/strategies.js` calls `/strategies/...` while every other `FrontEnd/src/api/*.js` file calls `/api/...`). Match the existing prefix for whichever router you're touching rather than "fixing" it unilaterally.

### Auth
JWT-based, via `python-jose`. `services/auth_service.py` centralizes token decoding and the `get_current_user` dependency that essentially all protected routes depend on. Passwords are hashed with `bcrypt` directly in `routers/auth.py` (not through the `passlib` `CryptContext` that's imported in `routers/users.py` but unused there). Tokens embed `sub` (email) and `user_id`; the frontend (`FrontEnd/src/state/AuthContext.jsx`) decodes the JWT payload client-side (no verification) just to read `sub`/`user_id` for display, and persists the raw token in `localStorage`.

### Ownership model
Every mutable resource chains back to a `User` via `owner_id`: `User` → `Project` (`owner_id`) → `Strategy` (`project_id`) → `BacktestResult` (`strategy_id`). There's no direct `owner_id` on `Strategy`/`BacktestResult`, so authorization checks walk the chain — e.g. `strategy.project.owner_id != user.id` (see `services/backtest_service.py`, `routers/strategies.py`, `routers/backtest.py`). Reuse this same walk-the-relationship-chain pattern for any new ownership check rather than adding a denormalized owner field.

### Strategy definition & execution
A `Strategy.parameters` field stores a JSON string like `{"name", "parameters": {...indicator params...}, "rules": {"entry": "...", "exit": "..."}}`. `services/strategy_executor.py`'s `StrategyExecutor` computes all five advertised indicators — SMA, EMA (`ema_period` → `ema`), RSI, Bollinger Bands, MACD (`macd_fast`/`macd_slow`/`macd_signal` → `macd`/`macd_signal_line`/`macd_hist`, built from `_calculate_ema` — reuse that helper rather than adding new `.ewm()` calls) — and evaluates entry/exit condition strings such as `"fast_ma > slow_ma"` **without `eval`** — it whitelists via regex and manually parses `column/number <op> column/number`. This is a deliberate security measure against code injection; any change to condition evaluation must preserve that no arbitrary expression can reach `eval`/`exec`. All five indicators are also selectable in the frontend's Strategy Builder (`FrontEnd/src/components/StrategyBuilder.jsx`) — keep the `INDICATORS` list, `handleSave`'s parameter mapping, and the engine's supported params in sync if you add another.

`_execute_trades` uses capital-based, all-in position sizing (max whole shares affordable, floor division after folding commission into the fill price — see the method for the exact formula) with configurable `commission_pct`/`slippage_pct` (defaults 0.1%/0.05%, passed from `BacktestRequest` down through `backtest_service.run_backtest`). It has no support for shorting, stop-loss/take-profit, or concurrent positions — one position at a time, long-only. `backtest()` returns `{'trades', 'metrics', 'signals', 'equity_curve'}`; `signals` and `equity_curve` are row-record lists (`{'date', ...}` per bar) built via the shared `_format_date` helper, and are persisted on `BacktestResult.signals`/`.equity_curve` (JSON columns).

`_calculate_metrics` computes `total_return`/`return_pct`/`final_capital`/`win_rate`/`num_trades` plus `max_drawdown_pct` and `sharpe_ratio` (bar-over-bar equity returns × √252, assumes daily bars and 0% risk-free rate — not configurable). `final_capital`/`total_return`/`return_pct` prefer the equity curve's last value over the sum of realized exit P&L whenever the curve is non-empty, so an open position at the last bar is still reflected — don't revert this to realized-only math, it was a deliberate fix for a case where the two used to silently disagree.

`services/backtest_service.py` orchestrates a full backtest run: loads `MarketData` rows for a ticker/date range into a pandas `DataFrame`, runs them through `StrategyExecutor`, and persists the resulting `BacktestResult`.

**Fixed:** `strategy.project.owner_id` / `backtest.strategy.project` ownership checks in `services/backtest_service.py`, `routers/backtest.py`, and `routers/strategies.py` (`update_strategy`) used to lazily traverse SQLAlchemy relationships inside async request handlers with no eager-loading configured (`database/models.py` declares no `lazy="selectin"`, `connection.py` uses a plain `async_sessionmaker`), which raised `MissingGreenlet` against a real async session and surfaced as an uncaught 500 rather than the intended 400/403. Every `select(Strategy)`/`select(BacktestResult)` that reaches `.project`/`.strategy` now chains `.options(selectinload(...))` (nested as `selectinload(BacktestResult.strategy).selectinload(Strategy.project)` where both hops are needed). If you add a new ownership check that walks the relationship chain, eager-load it the same way — the regression is covered by `tests/test_backtest_ownership.py` and `tests/test_strategy_ownership.py`, so a plain `select()` without `.options(...)` on this chain will fail the suite.

`services/data_service.py` has incomplete/broken implementations (e.g. calls `.to_dataframe()` on a plain list of ORM objects, which doesn't exist) — treat it as unfinished scaffolding, not a working reference.

### Frontend structure
- `src/api/*.js` — one module per backend resource, thin axios wrappers around the shared `client.js` (base URL from `VITE_API_BASE`, JWT injected via `setAuthToken`).
- `src/state/AuthContext.jsx` — the only global state; holds the JWT and exposes `login`/`register`/`logout`.
- `src/components/ProtectedRoute.jsx` — gates routes on `useAuth().isAuthenticated`; used in `App.jsx` for `/projects`, `/projects/:projectId/strategies`, `/strategies/:strategyId/backtest`.
- Page components under `src/pages/` correspond 1:1 with those routes.

### Data models (`BackEnd/database/models.py`)
`User` 1—N `Project` 1—N `Strategy` 1—N `BacktestResult`. `MarketData` is independent (keyed by `ticker`+`date`, unique-constrained), not owned by a user — it's shared reference data that any authenticated user can query/upload/delete via `routers/data.py`. `BacktestResult` stores `results` (metrics), `trades`, `signals`, and `equity_curve` as JSON columns. There's no migration tooling (`init_db()` only runs `Base.metadata.create_all`, which won't alter an existing table) — if you add/change columns, the local dev DB's tables need to be dropped and recreated manually.

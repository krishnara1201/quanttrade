# Async Task Execution (Celery + Redis) — Design

## Summary

Long-running operations — single-ticker backtests, portfolio backtests, CSV/Stooq bulk uploads, and Alpha Vantage imports — currently run synchronously inline inside their `async def` FastAPI route handlers. None of that CPU-bound work (pandas indicator/trade-simulation loops in particular) is offloaded to a thread or process, so it blocks the single shared event loop for the whole backend process: one user's large backtest (e.g. a full-history dataset back to 1970, ~14k daily bars) freezes every other concurrent request, not just their own. This was observed directly — a backtest over a 1970-dataset hung the server.

This design moves all four operations onto a Celery task queue backed by Redis (broker only, no Celery result backend — task status and results are persisted in Postgres, which the frontend already polls via existing/extended endpoints). This is chosen over a same-process `run_in_threadpool` fix because the project is explicitly planning to scale to multiple concurrent users soon, and a task queue gives durability across restarts, worker scaling independent of API replicas, and per-task time limits/retries that a same-process fix can't.

## Architecture

**New services** (`docker-compose.yml`):
- `redis` — `redis:7-alpine`, internal network only, used purely as the Celery broker (no result backend).
- `worker` — same build context/image as `backend` (so it picks up the same `uv`-managed deps), started with `celery -A celery_app.celery_app worker --loglevel=info --concurrency=2` instead of uvicorn. Concurrency comes from Celery's default **prefork** pool (real OS processes), matching the sandbox executor's existing preference for process isolation over threads when doing heavy/untrusted work.

**New `BackEnd/celery_app.py`** defines the `Celery` app:
```python
celery_app = Celery(
    "quanttrade",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=None,
)
celery_app.conf.update(
    task_track_started=True,
    task_time_limit=660,       # 11 min hard kill
    task_soft_time_limit=600,  # 10 min soft — catchable, mirrors the RLIMIT_CPU soft/hard gap in sandbox_executor.py
    worker_prefetch_multiplier=1,
)
```
`CELERY_BROKER_URL` is read the same way `DATABASE_URL` is read in `database/connection.py` today — `os.getenv` with a localhost fallback for bare `uv run uvicorn` dev, overridden to `redis://redis:6379/0` in `docker-compose.yml` for both `backend` (producer) and `worker` (consumer).

**Worker DB access:** the API process's existing async engine/sessionmaker (`database/connection.py`) is created once at import time — unsafe to reuse as-is in a Celery prefork worker, since asyncpg connections don't survive `fork()`. The worker instead builds its own `AsyncEngine`/`async_sessionmaker` **lazily**, via a module-level "create on first use" helper that only runs after the fork (inside the first task a given child process executes), not at worker startup. Each task body is a thin sync function that does `asyncio.run(_do_the_work(...))`, where `_do_the_work` is `async def` and reuses the existing async service-layer functions and session-per-request pattern unchanged.

New dependency: `uv add celery redis` in `BackEnd/` (runtime deps).

## Schema changes

- `BacktestResult` and `PortfolioBacktestResult` (`database/models.py`) each get:
  - `status` — `String`, default `"pending"`. Lifecycle: `pending → running → success | failed`.
  - `error_message` — `Text`, nullable.
  
  The row is now created **immediately at submit time** with `status="pending"` and the result-bearing columns (`results`, `trades`, `signals`, `equity_curve` / `allocations`, `per_ticker`) left at their existing JSON defaults (`{}`/`[]`) until the worker fills them in on success.

- **New table `DataImportJob`** — there's no existing entity to attach status to for CSV/Alpha Vantage imports:
  ```python
  class DataImportJob(Base):
      __tablename__ = "data_import_jobs"
      id = Column(Integer, primary_key=True)
      user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
      source = Column(String, nullable=False)      # "csv" | "alpha_vantage"
      ticker = Column(String, nullable=True)        # null for a multi-symbol Stooq .txt upload
      status = Column(String, default="pending")    # pending -> running -> success | failed
      result = Column(JSON, default=None)           # {ticker, inserted, skipped} or [ ...one per symbol... ]
      error_message = Column(Text, nullable=True)
      created_at = Column(DateTime, default=datetime.utcnow)
      updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
  ```
  `user_id` tracks who submitted the job (`MarketData` itself stays unowned, per the existing model) so a user's own `DataPage.jsx` knows what to poll.

No migration tooling exists in this project — these are new columns/a new table, so the local dev DB needs its tables dropped/recreated manually (or `docker compose down -v` for a fresh Postgres volume), same as any other schema change here.

## Service-layer refactor (split "submit" from "execute")

Each of the four operations splits into a synchronous "create pending row" half (runs in the request handler) and an "execute" half (runs inside the Celery task via `asyncio.run(...)`):

- **`backtest_service.py`**: `create_pending_backtest(strategy_id, ticker, start_date, end_date, initial_capital, commission_pct, slippage_pct, db, user)` does the ownership check (same `selectinload(Strategy.project)` eager-load, same `MissingGreenlet`-avoidance as today), parses/validates dates via `datetime.fromisoformat` (same Postgres-string-vs-timestamp guard as today), inserts a `BacktestResult` row with `status="pending"`, commits, returns the row. Any invalid input or ownership failure still surfaces as an immediate 400/403 from the request handler, never as a background failure.

  `execute_backtest(backtest_result_id)` runs inside the task: re-fetches the row + strategy (same eager-loading), sets `status="running"`, commits; loads `MarketData`, runs `StrategyExecutor.backtest()`, writes results onto the *same* row, sets `status="success"`, commits. On any exception, sets `status="failed"`, `error_message=str(e)`, commits — this is where the existing `except Exception → HTTPException(400, ...)` translation moves to, since a worker can't raise an HTTP exception.

- **`portfolio_backtest_service.py`**: same split, `create_pending_portfolio_backtest` / `execute_portfolio_backtest`, reusing the existing `_check_ticker_coverage`, `aggregate_equity_curves`, `aggregate_portfolio_metrics` logic unchanged inside the execute half. Ticker-coverage validation (which is cheap — min/max/count only, not a full data load) stays in the synchronous "create pending" half so a bad basket 400s immediately rather than after a worker round-trip.

- **`routers/backtest.py`**: `POST /run` and `POST /run-portfolio` change from `return await run_backtest(...)` to: call the `create_pending_*` function, then `run_backtest_task.delay(backtest_result.id)` / `run_portfolio_backtest_task.delay(portfolio_result.id)`, then return the pending row (now including `status`). Tasks take only the row id — everything else needed lives on that row/its relations, avoiding duplicated params in the Celery message.

- **`routers/data.py`**: `POST /upload-csv` still parses the multipart file and validates its shape synchronously in the handler (missing `ticker` for a header'd CSV still 400s immediately, as today) via the existing `_split_bar_groups`/`_parse_bar_dates` helpers, then creates a `DataImportJob` row (`status="pending"`) and enqueues a task passing the **file content as base64-encoded bytes** in the task args — not a shared-volume temp path, so `worker` stays decoupled from `backend`'s filesystem (relevant if they're ever split across hosts). `POST /import/{ticker}` creates a `DataImportJob` row and enqueues a task with just `ticker`, `api_key`, `user_id` — no payload-size concern, it's a network fetch. Both tasks funnel through the existing `_bulk_upsert_market_data` helper unchanged.

## Error handling, retries, time limits

- **No automatic retry** for backtest/portfolio/CSV tasks — failures are almost always business-logic (bad date range, ticker has no data, sandboxed strategy code error, malformed CSV), and retrying just reproduces the same failure. Mark `status="failed"`, `error_message=str(e)`.
- **One retry with backoff** for the Alpha Vantage import task only (`max_retries=2`, short delay), and only on connection/timeout exceptions — not on Alpha Vantage's own "bad symbol" JSON error payload, which is a real failure.
- `task_time_limit`/`task_soft_time_limit` (11 min hard / 10 min soft, set at the app level above) act as a safety net so a runaway task gets killed and its row marked `"failed"` with a timeout message, rather than sitting in `"running"` forever. Mirrors the soft-before-hard `RLIMIT_CPU` reasoning already used in `sandbox_executor.py`.
- A worker crash mid-task (container restart, OOM) leaves a row stuck in `"running"` with no automatic recovery in this design — no stale-task reaper. Accepted gap for now (YAGNI at current scale); revisit only if it becomes a real problem.
- Ownership checks stay entirely in the synchronous request-handler half — a task never needs to re-authorize, since it only runs after the row was already created under a validated owner.

## Backend API surface changes

- `POST /api/backtest/run`, `POST /api/backtest/run-portfolio` — response shape changes from the full result body to the pending row (`id`, `status`, etc.), immediately after creation.
- `GET /api/backtest/{id}`, `GET /api/backtest/portfolio/{id}` — unchanged endpoints, but now double as the poll target; response includes the new `status`/`error_message` fields, `null` result fields while pending.
- `GET /api/backtest/results/{strategy_id}`, `GET /api/backtest/portfolio/results/{strategy_id}` — unchanged, but list rows now include in-progress items.
- `POST /api/data/upload-csv`, `POST /api/data/import/{ticker}` — response shape changes from the immediate `{ticker, inserted, skipped}` (or list thereof) to `{job_id, status: "pending"}`.
- **New** `GET /api/data/jobs/{job_id}` — returns a `DataImportJob` row (status, result, error_message), scoped to the requesting user (`job.user_id != user.id` → 403, matching the ownership-check style used elsewhere even though `MarketData` itself has none).

## Frontend

- `api/backtest.js`/`api/data.js`: submit calls now resolve with `{id, status: "pending"}` / `{job_id, status: "pending"}`. Add one shared polling helper (e.g. `pollUntil(fetchFn, id, {intervalMs: 2000})`) used by all four flows.
- `StrategiesPage.jsx`: after submitting a backtest, show a "Running…" state and poll the existing detail endpoint until `status` is `success`/`failed`, then render as today.
- `BacktestResultsPage.jsx`: add a status badge (`pending`/`running`/`failed`, alongside the existing "Portfolio · N tickers" badge) for rows not yet `success`.
- `DataPage.jsx`: upload/import forms poll `GET /api/data/jobs/{job_id}` and show the same pending/running/success/failed progression before rendering the inserted/skipped summary they show synchronously today.

## Testing

- `create_pending_*`/`execute_*` halves unit-tested the same way `run_backtest`/`run_portfolio_backtest` are today — real `sqlite+aiosqlite`, no mocks.
- Celery tasks tested with `celery_app.conf.task_always_eager = True` in test config, so `.delay()` runs synchronously inline in the test process — no real Redis/worker needed in CI, consistent with this repo's no-mocks testing style.
- `DataImportJob` status transitions (`pending → running → success|failed`) get the same treatment as the backtest status columns.
- New `GET /api/data/jobs/{job_id}` ownership check tested the same way as existing ownership-check regressions (`tests/test_backtest_ownership.py` style).
- Any new/changed date-range filtering inside the split-out `execute_*` functions is hand-verified against real Postgres via `docker compose up`, per this repo's existing dialect-sensitivity guidance (SQLite tolerates raw-string date comparisons that Postgres/asyncpg rejects).

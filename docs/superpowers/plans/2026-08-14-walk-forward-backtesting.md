# Walk-Forward Backtesting + Benchmark Overlay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a walk-forward evaluation mode (expanding-window out-of-sample backtesting) for custom-code (ML) strategies, and a buy-and-hold benchmark overlay on every backtest equity chart (single-ticker, portfolio, and walk-forward).

**Architecture:** A new `WalkForwardBacktestResult` table + `services/walk_forward_service.py` orchestrator, mirroring the existing `PortfolioBacktestResult`/`portfolio_backtest_service.py` pattern. Each fold reuses the *existing, unmodified* sandbox (`services/sandbox_executor.py`) via a small refactor that splits `StrategyExecutor.backtest()`'s signal-generation step out into a reusable `generate_signals()` method, then calls the existing `_execute_trades()` directly on just that fold's test-window rows, with capital compounding across folds. A new module-level `benchmark_equity_curve()` function computes a buy-and-hold reference curve, wired into all three backtest result types.

**Tech Stack:** FastAPI, SQLAlchemy (async, Postgres/asyncpg in prod, sqlite+aiosqlite in tests), Celery + Redis, pandas, scikit-learn (inside the sandbox), React + Vite, Recharts.

**Spec:** `docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md`

## Global Constraints

- Walk-forward applies only to `custom_code` strategies; a rules-mode strategy is rejected with a 400: `"Walk-forward evaluation requires a custom-code strategy."`
- Fold scheme is expanding-window: initial train window = `max(365 days, 25% of the requested date range)`; folds step forward by `test_window_days`; a trailing remainder shorter than `test_window_days` is dropped, not turned into a short partial fold.
- Capital compounds across folds — each fold's test window starts from the previous fold's ending equity, producing one continuous stitched OOS equity curve.
- Leakage boundary is intentionally simple: each fold's `generate_signals(df)` call sees that fold's full train+test slice. No stricter `train_end_idx` contract (explicitly deferred in the spec).
- Benchmark = buy-and-hold of the same ticker (or, for portfolio, the same weighted basket), no commission/slippage modeling — a reference line, not a tradable strategy. Applies to single-ticker, portfolio, and walk-forward results alike.
- No pre-trained/uploaded models in this feature (explicitly deferred in the spec).
- No migration tooling exists (`init_db()` only runs `Base.metadata.create_all`) — new/changed columns require dropping and recreating local dev tables, or a fresh Postgres volume.
- Every service function that reaches `.project`/`.strategy` must eager-load via `selectinload(...)` — a plain `select()` without `.options(...)` on that chain raises `MissingGreenlet` against a real async session (see `tests/test_backtest_ownership.py`).
- Every `execute_*` async task function must never raise: catch-all `except Exception`, `await db.rollback()` before touching the row (a DB failure can leave the session mid-failed-transaction), `status="failed"`, `error_message=f"{type(e).__name__}: {e}"` (not bare `str(e)` — some exceptions stringify to `''`).
- No mocks in backend tests — real in-memory `sqlite+aiosqlite`, real sandbox subprocess execution, matching this repo's existing testing style throughout.
- No frontend test suite exists in this project — frontend tasks are verified via `npm run build` plus a final manual QA pass, matching precedent (`docs/superpowers/plans/2026-08-13-portfolio-backtests.md` Task 13).

---

### Task 1: `StrategyExecutor.generate_signals()` extraction + `benchmark_equity_curve()`

**Files:**
- Modify: `BackEnd/services/strategy_executor.py:58-119` (the `backtest()` method)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Produces: `StrategyExecutor.generate_signals(self, df: pd.DataFrame) -> pd.Series` (returns the per-bar signal series for rules or custom-code mode, without executing trades or computing metrics — mutates `df` in place for rules mode by adding indicator columns, same as `_calculate_indicators` already did). Consumed directly by Task 7 (`walk_forward_service.py`).
- Produces: module-level `benchmark_equity_curve(df: pd.DataFrame, initial_capital: float) -> List[Dict[str, Any]]` in `services/strategy_executor.py`. Consumed by Tasks 3, 4, and 7.
- Consumes: nothing new — `generate_signals` is the exact existing logic from `backtest()`'s mode branch (lines 79-97), moved into its own method; `backtest()` calls it internally so behavior is unchanged.

- [ ] **Step 1: Run the existing suite to establish the baseline**

Run (from `BackEnd/`): `uv run pytest tests/test_strategy_executor.py -v`
Expected: All tests PASS.

- [ ] **Step 2: Extract `generate_signals` and simplify `backtest()`**

In `BackEnd/services/strategy_executor.py`, replace the `backtest()` method (lines 58-119) with:

```python
    def backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0,
                 commission_pct: float = 0.1, slippage_pct: float = 0.05,
                 allow_short: bool = False, stop_loss_pct: float = None,
                 take_profit_pct: float = None) -> Dict[str, Any]:
        """
        Run backtest on market data

        Args:
            df: DataFrame with OHLCV data (must have 'close' column)
            initial_capital: Starting capital
            commission_pct: Commission as a percent of trade notional
            slippage_pct: Slippage as a percent applied against the trader on fill

        Returns:
            Results dict with trades, metrics, per-bar signals, and equity curve
        """
        self.validate()

        df = df.copy()
        df['signal'] = self.generate_signals(df)

        trades, equity_curve = self._execute_trades(
            df, initial_capital, commission_pct, slippage_pct,
            allow_short=allow_short, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
        )
        metrics = self._calculate_metrics(df, trades, initial_capital, equity_curve)

        signals = [
            {
                'date': self._format_date(df.index[i]),
                'close': float(df.iloc[i]['close']),
                'signal': int(df.iloc[i]['signal']),
            }
            for i in range(len(df))
        ]

        return {
            'trades': trades,
            'metrics': metrics,
            'signals': signals,
            'equity_curve': equity_curve,
        }

    def generate_signals(self, df: pd.DataFrame) -> pd.Series:
        """Compute the per-bar {1, -1, 0} signal Series for `df` — either the
        rules-mode indicator calculation + condition evaluation, or a
        custom-code sandbox call — without executing trades or computing
        metrics. This is the reusable unit walk-forward evaluation calls
        once per fold (services/walk_forward_service.py); `backtest()` above
        calls it too, for the ordinary single-shot case.

        For rules mode this mutates `df` in place by adding indicator
        columns (e.g. 'fast_ma', 'rsi') — the same side effect
        `_calculate_indicators` always had. Callers that need `df`
        untouched should pass a copy.
        """
        mode = self.config.get('mode', 'rules')

        if mode == 'custom_code':
            from services.sandbox_executor import run_custom_strategy, SandboxError
            try:
                return run_custom_strategy(self.code, df)
            except SandboxError as e:
                raise ValueError(str(e))

        params = self.config.get('parameters', {})
        rules = self.config.get('rules', {})
        self._calculate_indicators(df, params)

        signal = pd.Series(0, index=df.index)
        for i in range(1, len(df)):
            if self._evaluate_condition(rules['entry'], df, i):
                signal.iloc[i] = 1
            elif self._evaluate_condition(rules['exit'], df, i):
                signal.iloc[i] = -1
        return signal
```

- [ ] **Step 3: Run tests to verify no regression**

Run: `uv run pytest tests/test_strategy_executor.py -v`
Expected: All tests PASS (same set as Step 1 — confirms the refactor is behavior-preserving).

- [ ] **Step 4: Write the new `benchmark_equity_curve` tests**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_benchmark_equity_curve_buys_and_holds():
    from services.strategy_executor import benchmark_equity_curve
    df = make_price_df([100, 110, 90, 120])
    curve = benchmark_equity_curve(df, initial_capital=1000.0)
    assert len(curve) == 4
    assert curve[0]["equity"] == pytest.approx(1000.0)
    assert curve[1]["equity"] == pytest.approx(1100.0)
    assert curve[2]["equity"] == pytest.approx(900.0)
    assert curve[3]["equity"] == pytest.approx(1200.0)
    assert curve[0]["date"] == df.index[0].isoformat()


def test_benchmark_equity_curve_empty_df_returns_empty_list():
    from services.strategy_executor import benchmark_equity_curve
    empty = make_price_df([])
    assert benchmark_equity_curve(empty, initial_capital=1000.0) == []
```

- [ ] **Step 5: Run to verify the new tests fail**

Run: `uv run pytest tests/test_strategy_executor.py -k benchmark_equity_curve -v`
Expected: FAIL with `ImportError` / `cannot import name 'benchmark_equity_curve'`.

- [ ] **Step 6: Implement `benchmark_equity_curve`**

In `BackEnd/services/strategy_executor.py`, append after the existing `sharpe_ratio` function (end of file):

```python


def benchmark_equity_curve(df: pd.DataFrame, initial_capital: float) -> List[Dict[str, Any]]:
    """Buy-and-hold reference curve over `df`'s date range: buy as many
    shares as `initial_capital` affords at the first bar's close, then mark
    to market every bar. No commission/slippage modeling — this is a
    reference line for comparison, not a tradable strategy."""
    if df.empty:
        return []
    first_close = df['close'].iloc[0]
    shares = initial_capital / first_close
    return [
        {
            'date': idx.isoformat() if hasattr(idx, 'isoformat') else str(idx),
            'equity': float(shares * close),
        }
        for idx, close in zip(df.index, df['close'])
    ]
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_strategy_executor.py -v`
Expected: All tests PASS, including the two new ones.

- [ ] **Step 8: Commit**

```bash
cd BackEnd
git add services/strategy_executor.py tests/test_strategy_executor.py
git commit -m "Extract StrategyExecutor.generate_signals() and add benchmark_equity_curve()"
```

---

### Task 2: Data model — `WalkForwardBacktestResult` + benchmark columns

**Files:**
- Modify: `BackEnd/database/models.py`
- Test: `BackEnd/tests/test_walk_forward_service.py` (new file)

**Interfaces:**
- Produces: `WalkForwardBacktestResult` ORM class (importable from `database.models`), `Strategy.walk_forward_backtests` relationship, `BacktestResult.benchmark_equity_curve` and `PortfolioBacktestResult.benchmark_equity_curve` columns. Consumed by Tasks 3, 4, 6, 7, 9.

- [ ] **Step 1: Write the round-trip test**

Create `BackEnd/tests/test_walk_forward_service.py`:

```python
"""Tests for walk-forward (expanding-window out-of-sample) backtest
evaluation: fold boundary computation, per-fold execution with capital
compounding, and the buy-and-hold benchmark overlay. No mocks — real
in-memory sqlite+aiosqlite, real sandboxed strategy execution, matching
this repo's existing testing style (see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md).
"""
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import (
    Base, User, Project, Strategy, WalkForwardBacktestResult, MarketData,
)


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import worker_db
    monkeypatch.setattr(worker_db, "_session_factory", factory)

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_walk_forward_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="strat", project_id=project.id,
            parameters=json.dumps({"name": "strat", "mode": "custom_code"}),
            code="def generate_signals(df):\n    return df['close'] * 0\n",
        )
        db.add(strategy)
        await db.flush()

        wfr = WalkForwardBacktestResult(
            strategy_id=strategy.id,
            ticker="AAPL",
            start_date=datetime(2015, 1, 1),
            end_date=datetime(2020, 1, 1),
            test_window_days=180,
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            total_folds=5,
            folds_completed=2,
            folds=[{"fold_index": 0, "return_pct": 1.5}],
            trades=[{"type": "entry", "price": 100.0}],
            equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0, "fold_index": 0}],
            benchmark_equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0}],
            results={"return_pct": 5.0},
        )
        db.add(wfr)
        await db.commit()
        await db.refresh(wfr)

        result = await db.execute(
            select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == wfr.id)
        )
        loaded = result.scalars().first()
        assert loaded.ticker == "AAPL"
        assert loaded.test_window_days == 180
        assert loaded.total_folds == 5
        assert loaded.folds_completed == 2
        assert loaded.folds == [{"fold_index": 0, "return_pct": 1.5}]
        assert loaded.benchmark_equity_curve == [{"date": "2016-01-01T00:00:00", "equity": 10000.0}]
        assert loaded.status == "pending"  # default
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_walk_forward_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'WalkForwardBacktestResult'`.

- [ ] **Step 3: Add the model**

In `BackEnd/database/models.py`, add `benchmark_equity_curve` columns to the two existing result tables. In `BacktestResult` (currently lines 63-84), insert after the `equity_curve` line (line 80):

```python
    equity_curve = Column(JSON, default=[])  # Per-bar {date, equity} mark-to-market series
    benchmark_equity_curve = Column(JSON, default=[])  # Buy-and-hold {date, equity} reference series
```

In `PortfolioBacktestResult` (currently lines 87-106), insert after the `equity_curve` line (line 102):

```python
    equity_curve = Column(JSON, default=[])  # aggregate portfolio {date, equity} series
    benchmark_equity_curve = Column(JSON, default=[])  # aggregate buy-and-hold {date, equity} reference series
```

Update `Strategy` (currently lines 31-44) to add the third relationship, after `portfolio_backtests` (line 44):

```python
    backtests = relationship("BacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    portfolio_backtests = relationship("PortfolioBacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    walk_forward_backtests = relationship("WalkForwardBacktestResult", back_populates="strategy", cascade="all, delete-orphan")
```

Add the new table after `PortfolioBacktestResult` (after line 106, before `class DataImportJob`):

```python
class WalkForwardBacktestResult(Base):
    __tablename__ = "walk_forward_backtest_results"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    strategy = relationship("Strategy", back_populates="walk_forward_backtests")
    ticker = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    test_window_days = Column(Integer, nullable=False)
    initial_capital = Column(Float, nullable=False)
    commission_pct = Column(Float, nullable=False)
    slippage_pct = Column(Float, nullable=False)
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    total_folds = Column(Integer, nullable=True)       # set once fold boundaries are computed
    folds_completed = Column(Integer, default=0, nullable=False)  # incremented per fold, for progress polling
    folds = Column(JSON, default=[])                    # [{fold_index, train_start, train_end, test_start, test_end, return_pct, num_trades}]
    trades = Column(JSON, default=[])                   # pooled across all fold test windows, chronological
    equity_curve = Column(JSON, default=[])              # stitched OOS curve, each row tagged fold_index
    benchmark_equity_curve = Column(JSON, default=[])    # buy-and-hold over the same stitched period
    results = Column(JSON, default={})                   # aggregate metrics, same field names as BacktestResult.results
    status = Column(String, default="pending")           # pending -> running -> success | failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_walk_forward_service.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS (the two new nullable-with-defaults columns don't affect existing `BacktestResult`/`PortfolioBacktestResult` inserts).

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add database/models.py tests/test_walk_forward_service.py
git commit -m "Add WalkForwardBacktestResult table and benchmark_equity_curve columns"
```

---

### Task 3: Wire benchmark curve into single-ticker backtests

**Files:**
- Modify: `BackEnd/services/backtest_service.py:117-136`
- Modify: `BackEnd/routers/backtest.py:104-114`
- Test: `BackEnd/tests/test_backtest_ownership.py`

**Interfaces:**
- Consumes: `benchmark_equity_curve` from Task 1.

- [ ] **Step 1: Write the test**

Append to `BackEnd/tests/test_backtest_ownership.py`:

```python
@pytest.mark.asyncio
async def test_execute_backtest_persists_benchmark_equity_curve(session_factory, seeded):
    async with session_factory() as db:
        await backtest_service.execute_backtest(seeded["backtest_id"], db)

    async with session_factory() as db:
        result = await db.execute(select(BacktestResult).where(BacktestResult.id == seeded["backtest_id"]))
        record = result.scalars().first()

    # seeded fixture: closes [10, 11, 12, 13, 14] for Jan 1-5 2024 (end_date
    # is Jan 5), initial_capital 10000.0 -> 1000 shares bought at close=10.
    assert record.status == "success"
    assert len(record.benchmark_equity_curve) == 5
    assert record.benchmark_equity_curve[0]["equity"] == pytest.approx(10000.0)
    assert record.benchmark_equity_curve[-1]["equity"] == pytest.approx(14000.0)
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_backtest_ownership.py -k benchmark_equity_curve -v`
Expected: FAIL — `record.benchmark_equity_curve` is `[]` (the default), not populated.

- [ ] **Step 3: Wire it into `execute_backtest`**

In `BackEnd/services/backtest_service.py`, change the import on line 9:

```python
from services.strategy_executor import StrategyExecutor, benchmark_equity_curve
```

Replace lines 117-136 (from `executor = StrategyExecutor(...)` through the end of `execute_backtest`) with:

```python
        executor = StrategyExecutor(strategy.parameters, code=strategy.code)
        backtest_results = executor.backtest(
            df, initial_capital=record.initial_capital,
            commission_pct=record.commission_pct, slippage_pct=record.slippage_pct,
            allow_short=record.allow_short, stop_loss_pct=record.stop_loss_pct,
            take_profit_pct=record.take_profit_pct,
        )
        benchmark_curve = benchmark_equity_curve(df, record.initial_capital)
    except Exception as e:
        await db.rollback()
        record.status = "failed"
        record.error_message = f"{type(e).__name__}: {e}"
        await db.commit()
        return

    record.results = backtest_results['metrics']
    record.trades = backtest_results['trades']
    record.signals = backtest_results['signals']
    record.equity_curve = backtest_results['equity_curve']
    record.benchmark_equity_curve = benchmark_curve
    record.status = "success"
    await db.commit()
```

- [ ] **Step 4: Surface it in the API response**

In `BackEnd/routers/backtest.py`, in `get_backtest_detail` (lines 104-114), add a line after `'equity_curve': backtest.equity_curve,` (line 112):

```python
        'equity_curve': backtest.equity_curve,
        'benchmark_equity_curve': backtest.benchmark_equity_curve,
        'created_at': backtest.created_at.isoformat(),
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_backtest_ownership.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
cd BackEnd
git add services/backtest_service.py routers/backtest.py tests/test_backtest_ownership.py
git commit -m "Persist buy-and-hold benchmark curve on single-ticker backtests"
```

---

### Task 4: Wire benchmark curve into portfolio backtests

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py`

**Interfaces:**
- Consumes: `benchmark_equity_curve` (Task 1), `aggregate_equity_curves` (existing, same file).

- [ ] **Step 1: Write the test**

Append to `BackEnd/tests/test_portfolio_backtest_service.py` (after the existing `test_create_and_execute_portfolio_backtest_allocates_and_aggregates` test — read that test's tail first to match its exact assertion style before appending):

```python
@pytest.mark.asyncio
async def test_execute_portfolio_backtest_persists_benchmark_equity_curve(session_factory, portfolio_seeded):
    async with session_factory() as db:
        record = await portfolio_backtest_service.create_pending_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05", 10000.0, 0.1, 0.05,
            db, await _reload_user(session_factory, portfolio_seeded["user_id"]),
        )
        await portfolio_backtest_service.execute_portfolio_backtest(record.id, db)

    async with session_factory() as db:
        result = await db.execute(
            select(PortfolioBacktestResult).where(PortfolioBacktestResult.id == record.id)
        )
        loaded = result.scalars().first()

    # AAPL flat at 100 (sub_capital 5000 -> 50 shares), MSFT flat at 200
    # (sub_capital 5000 -> 25 shares) -> combined benchmark stays flat at
    # 10000 the whole period (both tickers flat-priced, per portfolio_seeded).
    assert loaded.status == "success"
    assert len(loaded.benchmark_equity_curve) == 5
    for point in loaded.benchmark_equity_curve:
        assert point["equity"] == pytest.approx(10000.0)
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -k benchmark_equity_curve -v`
Expected: FAIL — `loaded.benchmark_equity_curve` is `[]`.

- [ ] **Step 3: Wire it into `execute_portfolio_backtest`**

In `BackEnd/services/portfolio_backtest_service.py`, change the import on line 20:

```python
from services.strategy_executor import StrategyExecutor, benchmark_equity_curve, max_drawdown_pct, sharpe_ratio
```

Inside `execute_portfolio_backtest`'s per-ticker loop (currently lines 213-250), add a benchmark accumulator and computation. Replace the loop body from `allocated_capital[ticker] = sub_capital` (line 216) through `per_ticker_results[ticker] = {**ticker_result, "allocated_capital": sub_capital}` (line 250) with:

```python
            allocated_capital[ticker] = sub_capital

            data_result = await db.execute(
                select(MarketData).where(
                    MarketData.ticker == ticker,
                    MarketData.date >= record.start_date,
                    MarketData.date <= record.end_date,
                ).order_by(MarketData.date)
            )
            rows = data_result.scalars().all()
            if not rows:
                raise ValueError(
                    f"No market data found for {ticker} between {record.start_date.date()} and {record.end_date.date()}"
                )
            df = pd.DataFrame([
                {
                    "date": r.date, "open": float(r.open), "high": float(r.high),
                    "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
                }
                for r in rows
            ])
            df.set_index("date", inplace=True)

            try:
                executor = StrategyExecutor(strategy.parameters, code=strategy.code)
                ticker_result = executor.backtest(
                    df, initial_capital=sub_capital,
                    commission_pct=record.commission_pct, slippage_pct=record.slippage_pct,
                    allow_short=record.allow_short, stop_loss_pct=record.stop_loss_pct,
                    take_profit_pct=record.take_profit_pct,
                )
            except Exception as e:
                raise ValueError(f"Backtest execution failed for {ticker}: {str(e)}")

            per_ticker_results[ticker] = {**ticker_result, "allocated_capital": sub_capital}
            per_ticker_benchmark_curves[ticker] = benchmark_equity_curve(df, sub_capital)
```

Add the `per_ticker_benchmark_curves` accumulator alongside the existing `per_ticker_results`/`allocated_capital` initialization (currently line 210-211):

```python
        per_ticker_results: Dict[str, Any] = {}
        allocated_capital: Dict[str, float] = {}
        per_ticker_benchmark_curves: Dict[str, List[Dict[str, Any]]] = {}
```

After the loop, compute the aggregate benchmark curve alongside the existing aggregate equity curve (currently lines 252-256):

```python
        portfolio_equity_curve = aggregate_equity_curves(
            {t: r["equity_curve"] for t, r in per_ticker_results.items()},
            allocated_capital,
        )
        benchmark_curve = aggregate_equity_curves(per_ticker_benchmark_curves, allocated_capital)
        metrics = aggregate_portfolio_metrics(per_ticker_results, portfolio_equity_curve, record.initial_capital)
```

Finally, persist it alongside the other fields at the end of the function (currently lines 264-268):

```python
    record.results = metrics
    record.equity_curve = portfolio_equity_curve
    record.benchmark_equity_curve = benchmark_curve
    record.per_ticker = per_ticker_results
    record.status = "success"
    await db.commit()
```

- [ ] **Step 4: Surface it in `get_portfolio_backtest_detail`**

In the same file, in `get_portfolio_backtest_detail` (currently lines 312-322), add a line after `"equity_curve": record.equity_curve,` (line 319):

```python
        "equity_curve": record.equity_curve,
        "benchmark_equity_curve": record.benchmark_equity_curve,
        "per_ticker": record.per_ticker,
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v`
Expected: All tests PASS.

- [ ] **Step 6: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 7: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Persist aggregate buy-and-hold benchmark curve on portfolio backtests"
```

---

### Task 5: Fold boundary computation

**Files:**
- Create: `BackEnd/services/walk_forward_service.py`
- Test: `BackEnd/tests/test_walk_forward_service.py`

**Interfaces:**
- Produces: `compute_fold_boundaries(start_dt: datetime, end_dt: datetime, test_window_days: int) -> List[Dict[str, datetime]]` — each dict has `fold_index, train_start, train_end, test_start, test_end` (all `datetime`, `train_end`/`test_end` inclusive). Raises `ValueError` if the range fits zero folds. Consumed by Task 7.
- Produces: `estimate_fold_count(start_dt: datetime, end_dt: datetime, test_window_days: int) -> int` — a conservative (>=actual) fold-count estimate for sizing a Celery time limit before folds are computed. Consumed by Task 9.

- [ ] **Step 1: Write the tests**

Append to `BackEnd/tests/test_walk_forward_service.py`:

```python
from services.walk_forward_service import compute_fold_boundaries, estimate_fold_count


def test_compute_fold_boundaries_raises_when_range_too_short():
    with pytest.raises(ValueError, match="too short"):
        compute_fold_boundaries(datetime(2024, 1, 1), datetime(2024, 6, 1), test_window_days=90)


def test_compute_fold_boundaries_produces_contiguous_expanding_folds():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    folds = compute_fold_boundaries(start, end, test_window_days=180)

    assert len(folds) >= 5
    for i, fold in enumerate(folds):
        assert fold["fold_index"] == i
        assert fold["train_start"] == start
        assert fold["train_end"] == fold["test_start"] - timedelta(days=1)
        assert (fold["test_end"] - fold["test_start"]).days == 179  # 180-day inclusive window
        assert fold["test_end"] <= end
        if i > 0:
            assert fold["test_start"] == folds[i - 1]["test_end"] + timedelta(days=1)
            assert fold["train_end"] > folds[i - 1]["train_end"]  # expanding


def test_compute_fold_boundaries_drops_short_trailing_remainder():
    start = datetime(2015, 1, 1)
    # 365 (min train) + 180 (one full fold) + a 50-day remainder, too short
    # for a second 180-day fold -> exactly 1 fold, remainder dropped.
    end = start + timedelta(days=365) + timedelta(days=179) + timedelta(days=50)
    folds = compute_fold_boundaries(start, end, test_window_days=180)
    assert len(folds) == 1
    assert folds[0]["test_end"] < end


def test_estimate_fold_count_is_a_conservative_upper_bound():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    actual = len(compute_fold_boundaries(start, end, test_window_days=180))
    estimate = estimate_fold_count(start, end, test_window_days=180)
    assert estimate >= actual
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_walk_forward_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.walk_forward_service'`.

- [ ] **Step 3: Implement**

Create `BackEnd/services/walk_forward_service.py`:

```python
"""Walk-forward (expanding-window out-of-sample) evaluation for custom-code
(ML) strategies. Instead of fitting/predicting once over an entire date
range (which lets a model implicitly "see" the whole history before being
scored on any of it), this splits the range into expanding train/test
folds, re-runs StrategyExecutor.generate_signals() fresh each fold, and
stitches the out-of-sample results into one continuous equity curve with
capital compounding across folds.

Scoped to custom_code strategies only — see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List


def compute_fold_boundaries(
    start_dt: datetime, end_dt: datetime, test_window_days: int
) -> List[Dict[str, Any]]:
    """Expanding-window fold boundaries. The first fold's train window is
    max(365 days, 25% of the total range), anchored at start_dt; each
    fold's test window is test_window_days long (inclusive), stepping
    forward from the end of the previous fold's test window. A trailing
    remainder shorter than test_window_days is dropped rather than becoming
    a short partial fold.

    Returns a list of {fold_index, train_start, train_end, test_start,
    test_end} dicts (train_end/test_end inclusive).

    Raises:
        ValueError: the range doesn't fit even one full fold.
    """
    total_days = (end_dt - start_dt).days
    initial_train_days = max(365, int(total_days * 0.25))

    folds = []
    fold_index = 0
    test_start = start_dt + timedelta(days=initial_train_days)
    while True:
        test_end = test_start + timedelta(days=test_window_days - 1)
        if test_end > end_dt:
            break
        folds.append({
            "fold_index": fold_index,
            "train_start": start_dt,
            "train_end": test_start - timedelta(days=1),
            "test_start": test_start,
            "test_end": test_end,
        })
        fold_index += 1
        test_start = test_end + timedelta(days=1)

    if not folds:
        raise ValueError("date range too short for the requested test window")
    return folds


def estimate_fold_count(start_dt: datetime, end_dt: datetime, test_window_days: int) -> int:
    """Conservative (over-)estimate of fold count, used to size the Celery
    task's time limit before folds are actually computed (that requires the
    market data itself, which isn't loaded until the task runs). Deliberately
    ignores the initial-train-window subtraction that compute_fold_boundaries
    applies, so this always estimates at or above the real fold count —
    erring toward a longer, safer time limit rather than a tight one that
    could clip a real run."""
    total_days = (end_dt - start_dt).days
    return max(1, -(-total_days // test_window_days))  # ceiling division
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_walk_forward_service.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add services/walk_forward_service.py tests/test_walk_forward_service.py
git commit -m "Add walk-forward fold boundary computation"
```

---

### Task 6: Walk-forward CRUD service functions

**Files:**
- Modify: `BackEnd/services/walk_forward_service.py`
- Test: `BackEnd/tests/test_walk_forward_service.py`

**Interfaces:**
- Produces: `create_pending_walk_forward_backtest(strategy_id, ticker, start_date, end_date, test_window_days, initial_capital, commission_pct, slippage_pct, db, user, *, allow_short=False, stop_loss_pct=None, take_profit_pct=None) -> WalkForwardBacktestResult`, `get_walk_forward_backtest_results(strategy_id, db, user) -> List[Dict]`, `get_walk_forward_backtest_detail(walk_forward_backtest_id, db, user) -> Dict`. All raise `ValueError` for ownership/validation failures (translated to HTTP by the router, Task 9). Consumed by Task 9.
- Consumes: `WalkForwardBacktestResult` (Task 2).

- [ ] **Step 1: Write the tests**

Append to `BackEnd/tests/test_walk_forward_service.py`:

```python
from services import walk_forward_service


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest_asyncio.fixture
async def custom_code_strategy_seeded(session_factory):
    """User/project/custom-code strategy plus enough clean daily bars for
    multiple walk-forward folds (5 years of calendar days)."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="ml-strat", project_id=project.id,
            parameters=json.dumps({"name": "ml-strat", "mode": "custom_code"}),
            code=(
                "def generate_signals(df):\n"
                "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
                "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
                "    return up - down\n"
            ),
        )
        db.add(strategy)
        await db.flush()

        rules_strategy = Strategy(
            name="rules-strat", project_id=project.id,
            parameters=json.dumps({
                "name": "rules-strat", "parameters": {},
                "rules": {"entry": "close > 0", "exit": "close < 0"},
            }),
        )
        db.add(rules_strategy)
        await db.flush()

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close=str(100 + (i % 10)), volume="1000",
            ))
        await db.commit()
        return {
            "user_id": user.id,
            "custom_code_strategy_id": strategy.id,
            "rules_strategy_id": rules_strategy.id,
        }


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_persists_row(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
    assert record.status == "pending"
    assert record.ticker == "AAPL"
    assert record.test_window_days == 180
    assert record.folds_completed == 0


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_rejects_rules_mode_strategy(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        with pytest.raises(ValueError, match="custom-code strategy"):
            await walk_forward_service.create_pending_walk_forward_backtest(
                custom_code_strategy_seeded["rules_strategy_id"], "AAPL",
                "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
            )


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_rejects_unauthorized_user(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        other_user = User(name="Eve", email="eve@example.com", password_hash="x")
        db.add(other_user)
        await db.commit()
        await db.refresh(other_user)

        with pytest.raises(ValueError, match="Unauthorized"):
            await walk_forward_service.create_pending_walk_forward_backtest(
                custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
                "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, other_user,
            )


@pytest.mark.asyncio
async def test_get_walk_forward_backtest_results_and_detail_do_not_raise_missing_greenlet(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )

    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        results = await walk_forward_service.get_walk_forward_backtest_results(
            custom_code_strategy_seeded["custom_code_strategy_id"], db, user,
        )
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        detail = await walk_forward_service.get_walk_forward_backtest_detail(record.id, db, user)
        assert detail["id"] == record.id
        assert detail["status"] == "pending"
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_walk_forward_service.py -v`
Expected: FAIL — `AttributeError`/`ImportError` for the not-yet-defined functions.

- [ ] **Step 3: Implement**

In `BackEnd/services/walk_forward_service.py`, add imports at the top (after the existing `from typing import ...` line) and the three functions at the end of the file:

```python
import json
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Strategy, User, WalkForwardBacktestResult
```

Append to the end of the file:

```python


async def create_pending_walk_forward_backtest(
    strategy_id: int,
    ticker: str,
    start_date: str,
    end_date: str,
    test_window_days: int,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    db: AsyncSession,
    user: User,
    *,
    allow_short: bool = False,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
) -> WalkForwardBacktestResult:
    """Validate ownership/mode/input and insert a pending
    WalkForwardBacktestResult row. The actual fold-by-fold execution
    happens later in execute_walk_forward, run out-of-process by a Celery
    worker. Raises ValueError for any validation/ownership failure —
    callers (the router) translate that to an HTTP 400/403/404."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise ValueError("Strategy not found")
    if strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    config = json.loads(strategy.parameters) if isinstance(strategy.parameters, str) else strategy.parameters
    if config.get("mode") != "custom_code":
        raise ValueError("Walk-forward evaluation requires a custom-code strategy")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise ValueError("start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    if test_window_days <= 0:
        raise ValueError("test_window_days must be positive")
    if stop_loss_pct is not None and stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be positive")

    record = WalkForwardBacktestResult(
        strategy_id=strategy_id,
        ticker=ticker,
        start_date=start_dt,
        end_date=end_dt,
        test_window_days=test_window_days,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allow_short=allow_short,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record


async def get_walk_forward_backtest_results(strategy_id: int, db: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """Summary rows (no folds/trades/equity_curve payload) for all
    walk-forward backtests of a strategy."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if not strategy or strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    result = await db.execute(
        select(WalkForwardBacktestResult)
        .where(WalkForwardBacktestResult.strategy_id == strategy_id)
        .order_by(WalkForwardBacktestResult.created_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "ticker": r.ticker,
            "test_window_days": r.test_window_days,
            "status": r.status,
            "folds_completed": r.folds_completed,
            "total_folds": r.total_folds,
            "metrics": r.results,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


async def get_walk_forward_backtest_detail(walk_forward_backtest_id: int, db: AsyncSession, user: User) -> Dict[str, Any]:
    """Full detail for one walk-forward backtest, including the per-fold
    breakdown, stitched trades/equity curve, and benchmark curve."""
    result = await db.execute(
        select(WalkForwardBacktestResult)
        .options(selectinload(WalkForwardBacktestResult.strategy).selectinload(Strategy.project))
        .where(WalkForwardBacktestResult.id == walk_forward_backtest_id)
    )
    record = result.scalars().first()
    if not record:
        raise ValueError("Walk-forward backtest not found")
    if record.strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "ticker": record.ticker,
        "test_window_days": record.test_window_days,
        "status": record.status,
        "error_message": record.error_message,
        "folds_completed": record.folds_completed,
        "total_folds": record.total_folds,
        "folds": record.folds,
        "metrics": record.results,
        "trades": record.trades,
        "equity_curve": record.equity_curve,
        "benchmark_equity_curve": record.benchmark_equity_curve,
        "created_at": record.created_at.isoformat(),
    }
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_walk_forward_service.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add services/walk_forward_service.py tests/test_walk_forward_service.py
git commit -m "Add walk-forward backtest ownership/CRUD service functions"
```

---

### Task 7: Fold execution engine (`execute_walk_forward`)

**Files:**
- Modify: `BackEnd/services/walk_forward_service.py`
- Test: `BackEnd/tests/test_walk_forward_service.py`

**Interfaces:**
- Produces: `execute_walk_forward(walk_forward_backtest_id: int, db: AsyncSession) -> None` — never raises; writes outcome onto the row (`status`, `folds`, `trades`, `equity_curve`, `benchmark_equity_curve`, `results`, or `error_message`). Consumed by Task 8.
- Consumes: `compute_fold_boundaries` (Task 5), `StrategyExecutor.generate_signals`/`_execute_trades`/`_calculate_metrics` and `benchmark_equity_curve` (Task 1).

- [ ] **Step 1: Write the tests**

Append to `BackEnd/tests/test_walk_forward_service.py`:

```python
@pytest.mark.asyncio
async def test_execute_walk_forward_marks_row_failed_when_no_market_data(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "MSFT",  # no MarketData seeded for MSFT
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "No market data" in loaded.error_message


@pytest.mark.asyncio
async def test_execute_walk_forward_marks_row_failed_when_range_too_short(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2015-06-01", 90, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "too short" in loaded.error_message


@pytest.mark.asyncio
async def test_execute_walk_forward_end_to_end_compounds_capital_and_tracks_progress(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()

    assert loaded.status == "success"
    assert loaded.total_folds >= 5
    assert loaded.folds_completed == loaded.total_folds
    assert len(loaded.folds) == loaded.total_folds
    for i, fold in enumerate(loaded.folds):
        assert fold["fold_index"] == i
        assert "return_pct" in fold
        assert "num_trades" in fold
    # equity curve is continuous across folds and tagged with fold_index
    assert all("fold_index" in point for point in loaded.equity_curve)
    assert loaded.equity_curve[0]["fold_index"] == 0
    assert loaded.equity_curve[-1]["fold_index"] == loaded.total_folds - 1
    # benchmark curve covers the same stitched OOS period
    assert len(loaded.benchmark_equity_curve) > 0
    assert loaded.benchmark_equity_curve[0]["date"] == loaded.equity_curve[0]["date"]
    assert "return_pct" in loaded.results
    assert "sharpe_ratio" in loaded.results
```

A fold-level failure also needs its own fixture — a custom-code strategy whose `generate_signals` always raises, so the test can assert the whole run fails with a message naming which fold failed:

```python
@pytest_asyncio.fixture
async def broken_custom_code_strategy_seeded(session_factory):
    """A custom-code strategy whose generate_signals() always raises —
    used to verify a fold failure fails the whole walk-forward run with a
    message naming which fold failed."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="broken", project_id=project.id,
            parameters=json.dumps({"name": "broken", "mode": "custom_code"}),
            code="def generate_signals(df):\n    raise ValueError('boom')\n",
        )
        db.add(strategy)
        await db.flush()

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close="100", volume="1000",
            ))
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


@pytest.mark.asyncio
async def test_execute_walk_forward_fails_whole_run_naming_the_fold(session_factory, broken_custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, broken_custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            broken_custom_code_strategy_seeded["strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "Fold 1" in loaded.error_message
    assert loaded.folds_completed == 0
```

- [ ] **Step 2: Run to verify the new tests fail**

Run (from `BackEnd/`): `uv run pytest tests/test_walk_forward_service.py -v`
Expected: FAIL — `execute_walk_forward` doesn't exist yet (`AttributeError`).

- [ ] **Step 3: Implement `execute_walk_forward`**

In `BackEnd/services/walk_forward_service.py`, add imports at the top (extend the existing import block):

```python
import pandas as pd

from database.models import MarketData
from services.strategy_executor import StrategyExecutor, benchmark_equity_curve
```

Append to the end of the file:

```python


async def execute_walk_forward(walk_forward_backtest_id: int, db: AsyncSession) -> None:
    """Run the fold loop for an already-created pending
    WalkForwardBacktestResult row and write the outcome back onto that row.
    Runs inside a Celery worker via asyncio.run() (see tasks.py). Never
    raises — any failure (no market data, date range too short, a fold's
    sandbox call failing) is recorded on the row as status='failed' +
    error_message, since a worker has no HTTP response to raise into."""
    result = await db.execute(
        select(WalkForwardBacktestResult)
        .options(selectinload(WalkForwardBacktestResult.strategy))
        .where(WalkForwardBacktestResult.id == walk_forward_backtest_id)
    )
    record = result.scalars().first()
    if record is None:
        return

    record.status = "running"
    await db.commit()

    try:
        data_result = await db.execute(
            select(MarketData).where(
                MarketData.ticker == record.ticker,
                MarketData.date >= record.start_date,
                MarketData.date <= record.end_date,
            ).order_by(MarketData.date)
        )
        rows = data_result.scalars().all()
        if not rows:
            raise ValueError(
                f"No market data found for {record.ticker} between {record.start_date.date()} and {record.end_date.date()}"
            )
        df = pd.DataFrame([
            {
                "date": r.date, "open": float(r.open), "high": float(r.high),
                "low": float(r.low), "close": float(r.close), "volume": float(r.volume),
            }
            for r in rows
        ])
        df.set_index("date", inplace=True)

        folds = compute_fold_boundaries(record.start_date, record.end_date, record.test_window_days)
        record.total_folds = len(folds)
        await db.commit()

        strategy = record.strategy
        executor = StrategyExecutor(strategy.parameters, code=strategy.code)

        running_capital = record.initial_capital
        all_trades: List[Dict[str, Any]] = []
        all_equity: List[Dict[str, Any]] = []
        folds_summary: List[Dict[str, Any]] = []

        for fold in folds:
            fold_start_capital = running_capital
            try:
                growing_slice = df.loc[df.index <= fold["test_end"]].copy()
                growing_slice["signal"] = executor.generate_signals(growing_slice)
                test_mask = (
                    (growing_slice.index >= fold["test_start"]) & (growing_slice.index <= fold["test_end"])
                )
                test_slice = growing_slice.loc[test_mask]
                if test_slice.empty:
                    raise ValueError("no market data in this fold's test window")

                fold_trades, fold_equity = executor._execute_trades(
                    test_slice, fold_start_capital, record.commission_pct, record.slippage_pct,
                    allow_short=record.allow_short, stop_loss_pct=record.stop_loss_pct,
                    take_profit_pct=record.take_profit_pct,
                )
            except Exception as e:
                raise ValueError(
                    f"Fold {fold['fold_index'] + 1}/{len(folds)} failed: {type(e).__name__}: {e}"
                )

            for point in fold_equity:
                point["fold_index"] = fold["fold_index"]

            running_capital = fold_equity[-1]["equity"] if fold_equity else fold_start_capital
            folds_summary.append({
                "fold_index": fold["fold_index"],
                "train_start": fold["train_start"].isoformat(),
                "train_end": fold["train_end"].isoformat(),
                "test_start": fold["test_start"].isoformat(),
                "test_end": fold["test_end"].isoformat(),
                "return_pct": (
                    float((running_capital - fold_start_capital) / fold_start_capital * 100)
                    if fold_start_capital else 0.0
                ),
                "num_trades": len([t for t in fold_trades if t["type"] == "exit"]),
            })
            all_trades.extend(fold_trades)
            all_equity.extend(fold_equity)

            record.folds_completed += 1
            await db.commit()

        # _calculate_metrics doesn't actually use its `df` parameter (only
        # trades/initial_capital/equity_curve) — see strategy_executor.py.
        metrics = executor._calculate_metrics(None, all_trades, record.initial_capital, all_equity)
        benchmark_df = df.loc[
            (df.index >= folds[0]["test_start"]) & (df.index <= folds[-1]["test_end"])
        ]
        benchmark_curve = benchmark_equity_curve(benchmark_df, record.initial_capital)
    except Exception as e:
        await db.rollback()
        record.status = "failed"
        record.error_message = f"{type(e).__name__}: {e}"
        await db.commit()
        return

    record.folds = folds_summary
    record.trades = all_trades
    record.equity_curve = all_equity
    record.benchmark_equity_curve = benchmark_curve
    record.results = metrics
    record.status = "success"
    await db.commit()
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_walk_forward_service.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add services/walk_forward_service.py tests/test_walk_forward_service.py
git commit -m "Add walk-forward fold execution engine with capital compounding"
```

---

### Task 8: Celery task

**Files:**
- Modify: `BackEnd/tasks.py`
- Test: `BackEnd/tests/test_celery_tasks.py`

**Interfaces:**
- Produces: `walk_forward_task(walk_forward_backtest_id: int) -> None` (Celery task, name `"tasks.walk_forward_task"`). Consumed by Task 9.

- [ ] **Step 1: Write the test**

Append to `BackEnd/tests/test_celery_tasks.py` (mirror the existing `seeded`/`test_run_backtest_task_marks_row_success` pattern in this file — read the file's `seeded` fixture first; it seeds a rules-mode strategy, so add a parallel fixture for a custom-code one):

```python
@pytest_asyncio.fixture
async def walk_forward_seeded(session_factory):
    from database.models import WalkForwardBacktestResult
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="ml", project_id=project.id,
            parameters=json.dumps({"name": "ml", "mode": "custom_code"}),
            code=(
                "def generate_signals(df):\n"
                "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
                "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
                "    return up - down\n"
            ),
        )
        db.add(strategy)
        await db.flush()

        wf = WalkForwardBacktestResult(
            strategy_id=strategy.id, ticker="AAPL",
            start_date=datetime(2015, 1, 1), end_date=datetime(2020, 1, 1),
            test_window_days=180, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05,
        )
        db.add(wf)

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close=str(100 + (i % 10)), volume="1000",
            ))
        await db.commit()
        return {"walk_forward_id": wf.id}


@pytest.mark.asyncio
async def test_walk_forward_task_marks_row_success(session_factory, walk_forward_seeded):
    from tasks import walk_forward_task
    await asyncio.to_thread(walk_forward_task.delay, walk_forward_seeded["walk_forward_id"])

    async with session_factory() as db:
        from database.models import WalkForwardBacktestResult
        result = await db.execute(
            select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == walk_forward_seeded["walk_forward_id"])
        )
        record = result.scalars().first()

    assert record.status == "success"
    assert record.folds_completed == record.total_folds
```

Add `from datetime import timedelta` to this test file's existing `from datetime import datetime` import line if not already present, and `from sqlalchemy import select` if not already imported at module level (check the top of the file first — the existing `test_run_backtest_task_marks_row_success` test imports `select` locally inside the test function, so follow that same local-import style rather than adding a module-level import).

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_celery_tasks.py -k walk_forward -v`
Expected: FAIL — `ImportError: cannot import name 'walk_forward_task'`.

- [ ] **Step 3: Implement**

In `BackEnd/tasks.py`, add after `run_portfolio_backtest_task` (after line 35):

```python
@celery_app.task(name="tasks.walk_forward_task")
def walk_forward_task(walk_forward_backtest_id: int) -> None:
    from services.walk_forward_service import execute_walk_forward
    asyncio.run(_run_with_session(execute_walk_forward, walk_forward_backtest_id))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_celery_tasks.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add tasks.py tests/test_celery_tasks.py
git commit -m "Add walk_forward_task Celery task"
```

---

### Task 9: Router endpoints

**Files:**
- Modify: `BackEnd/routers/backtest.py`
- Test: `BackEnd/tests/test_backtest_ownership.py`

**Interfaces:**
- Produces: `POST /api/backtest/run-walk-forward`, `GET /api/backtest/walk-forward/results/{strategy_id}`, `GET /api/backtest/walk-forward/{walk_forward_backtest_id}`. Consumed by Task 10 (frontend API client).
- Consumes: `create_pending_walk_forward_backtest`/`get_walk_forward_backtest_results`/`get_walk_forward_backtest_detail` (Task 6), `walk_forward_task` (Task 8), `estimate_fold_count` (Task 5).

- [ ] **Step 1: Write the test**

Append to `BackEnd/tests/test_backtest_ownership.py` (mirror `test_run_backtest_endpoint_marks_row_failed_when_delay_raises`, adapted for the walk-forward endpoint and its `apply_async` call):

```python
@pytest.mark.asyncio
async def test_run_walk_forward_endpoint_rejects_rules_mode_strategy(session_factory, seeded):
    """`seeded`'s strategy is rules-mode (fast_ma/slow_ma) — walk-forward
    should reject it with a 400, not attempt to enqueue anything."""
    from fastapi import HTTPException
    from routers.backtest import WalkForwardBacktestRequest, run_walk_forward_backtest_endpoint

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        req = WalkForwardBacktestRequest(
            strategy_id=seeded["strategy_id"], ticker="TEST",
            start_date="2015-01-01", end_date="2020-01-01", test_window_days=180,
        )
        with pytest.raises(HTTPException) as exc_info:
            await run_walk_forward_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 400
        assert "custom-code strategy" in exc_info.value.detail


@pytest.mark.asyncio
async def test_run_walk_forward_endpoint_marks_row_failed_when_apply_async_raises(session_factory, seeded, monkeypatch):
    """Same enqueue-failure contract as the other three async task types
    (see routers/backtest.py and routers/data.py) — if the broker is
    unreachable, the already-committed pending row is marked failed and the
    endpoint raises a 503 instead of an opaque 500."""
    import json
    from fastapi import HTTPException
    from sqlalchemy import select as _select
    from database.models import Project, Strategy
    from routers.backtest import WalkForwardBacktestRequest, run_walk_forward_backtest_endpoint, walk_forward_task

    # seeded's own strategy is rules-mode (see the rejection test above) —
    # build a sibling custom-code strategy in the same project so this test
    # can exercise the enqueue-failure path instead of the mode check.
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        proj_result = await db.execute(_select(Project).where(Project.owner_id == user.id))
        project = proj_result.scalars().first()
        strategy = Strategy(
            name="ml", project_id=project.id,
            parameters=json.dumps({"name": "ml", "mode": "custom_code"}),
            code="def generate_signals(df):\n    return df['close'] * 0\n",
        )
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)

    def broken_apply_async(*args, **kwargs):
        raise ConnectionError("could not connect to redis")

    monkeypatch.setattr(walk_forward_task, "apply_async", broken_apply_async)

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        req = WalkForwardBacktestRequest(
            strategy_id=strategy.id, ticker="TEST",
            start_date="2015-01-01", end_date="2020-01-01", test_window_days=180,
        )
        with pytest.raises(HTTPException) as exc_info:
            await run_walk_forward_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 503
```

- [ ] **Step 2: Run to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_backtest_ownership.py -k walk_forward -v`
Expected: FAIL — `ImportError: cannot import name 'WalkForwardBacktestRequest'`.

- [ ] **Step 3: Implement**

In `BackEnd/routers/backtest.py`, update the imports (lines 1-15):

```python
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Strategy, BacktestResult
from database.connection import get_db
from services.auth_service import get_current_user
from services.backtest_service import create_pending_backtest, get_backtest_results
from services.portfolio_backtest_service import (
    create_pending_portfolio_backtest, get_portfolio_backtest_results, get_portfolio_backtest_detail,
)
from services.walk_forward_service import (
    create_pending_walk_forward_backtest, get_walk_forward_backtest_results,
    get_walk_forward_backtest_detail, estimate_fold_count,
)
from services.sandbox_executor import DEFAULT_TIMEOUT_S
from tasks import run_backtest_task, run_portfolio_backtest_task, walk_forward_task
from datetime import datetime
```

Add the request model after `PortfolioBacktestRequest` (after line 45):

```python
class WalkForwardBacktestRequest(BaseModel):
    strategy_id: int
    ticker: str
    start_date: str
    end_date: str
    test_window_days: int
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
```

Add the three endpoints at the end of the file (after the existing `get_portfolio_backtest_detail_endpoint`):

```python
@router.post("/run-walk-forward")
async def run_walk_forward_backtest_endpoint(
    req: WalkForwardBacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Create a pending walk-forward backtest and enqueue it for async
    execution. Fold computation (and therefore date-range validation) happens
    inside the task, not here — see execute_walk_forward."""
    try:
        record = await create_pending_walk_forward_backtest(
            req.strategy_id, req.ticker, req.start_date, req.end_date, req.test_window_days,
            req.initial_capital, req.commission_pct, req.slippage_pct, db, user,
            allow_short=req.allow_short, stop_loss_pct=req.stop_loss_pct, take_profit_pct=req.take_profit_pct,
        )
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

    start_dt = datetime.fromisoformat(req.start_date)
    end_dt = datetime.fromisoformat(req.end_date)
    estimated_folds = estimate_fold_count(start_dt, end_dt, req.test_window_days)
    # Each fold is one sandboxed subprocess call (budgeted DEFAULT_TIMEOUT_S)
    # plus a small margin; +30s overall slack for the DB round trips between
    # folds. Deliberately generous since estimated_folds already
    # over-estimates (see estimate_fold_count's docstring).
    time_limit = estimated_folds * (DEFAULT_TIMEOUT_S + 5) + 30
    try:
        walk_forward_task.apply_async(
            args=[record.id], time_limit=time_limit, soft_time_limit=max(time_limit - 30, 1),
        )
    except Exception as e:
        record.status = "failed"
        record.error_message = f"Could not enqueue task: {e}"
        await db.commit()
        raise HTTPException(status_code=503, detail="Task queue unavailable, please try again")
    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "ticker": record.ticker,
        "status": record.status,
        "created_at": record.created_at.isoformat(),
    }

@router.get("/walk-forward/results/{strategy_id}")
async def get_walk_forward_backtest_results_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get all walk-forward backtest results for a strategy"""
    try:
        return await get_walk_forward_backtest_results(strategy_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

@router.get("/walk-forward/{walk_forward_backtest_id}")
async def get_walk_forward_backtest_detail_endpoint(
    walk_forward_backtest_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed walk-forward backtest results, including per-fold breakdown"""
    try:
        return await get_walk_forward_backtest_detail(walk_forward_backtest_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_backtest_ownership.py -v`
Expected: All tests PASS.

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All tests PASS.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add routers/backtest.py tests/test_backtest_ownership.py
git commit -m "Add walk-forward backtest router endpoints"
```

---

### Task 10: Frontend API client

**Files:**
- Modify: `FrontEnd/src/api/backtest.js`

**Interfaces:**
- Produces: `runWalkForwardBacktest(strategyId, ticker, startDate, endDate, testWindowDays, initialCapital, commissionPct, slippagePct, allowShort, stopLossPct, takeProfitPct)`, `getWalkForwardBacktestResults(strategyId)`, `getWalkForwardBacktestDetail(walkForwardBacktestId)`. Consumed by Tasks 12, 13.

- [ ] **Step 1: Add the functions**

Append to `FrontEnd/src/api/backtest.js`:

```javascript
export async function runWalkForwardBacktest(strategyId, ticker, startDate, endDate, testWindowDays, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05, allowShort = false, stopLossPct = null, takeProfitPct = null) {
  const { data } = await client.post('/api/backtest/run-walk-forward', {
    strategy_id: strategyId,
    ticker,
    start_date: startDate,
    end_date: endDate,
    test_window_days: testWindowDays,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
    allow_short: allowShort,
    stop_loss_pct: stopLossPct,
    take_profit_pct: takeProfitPct,
  });
  return data;
}

export async function getWalkForwardBacktestResults(strategyId) {
  const { data } = await client.get(`/api/backtest/walk-forward/results/${strategyId}`);
  return data;
}

export async function getWalkForwardBacktestDetail(walkForwardBacktestId) {
  const { data } = await client.get(`/api/backtest/walk-forward/${walkForwardBacktestId}`);
  return data;
}
```

- [ ] **Step 2: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
cd FrontEnd
git add src/api/backtest.js
git commit -m "Add frontend API client for walk-forward backtests"
```

---

### Task 11: `BacktestChart.jsx` — benchmark line + equity-only rendering

**Files:**
- Modify: `FrontEnd/src/components/BacktestChart.jsx` (full-file rewrite — the change touches the top-of-file guard clause, the equity data mapping, and adds a prop, so a full rewrite is clearer than a line-ranged diff for this ~145-line file)

**Interfaces:**
- Produces: `BacktestChart` gains two new optional props — `benchmarkEquityCurve` (default `[]`, renders a dashed "Buy & Hold" line in the equity panel when non-empty) and `equityName` (default `'Equity'`, the equity line's legend label). Also relaxes the "no data" guard: the component now renders the equity panel even when `data` (price/signals) is empty, as long as `equityCurve` is non-empty — needed for the portfolio and walk-forward aggregate views (Task 13), which have no per-bar price/signal series, only an equity curve. Consumed by Task 13.
- Consumes: nothing new.

- [ ] **Step 1: Write the manual verification plan (no automated frontend test suite exists in this project)**

This task is verified via `npm run build` plus the Task 14 manual QA pass (per this plan's Global Constraints — no frontend test suite exists).

- [ ] **Step 2: Rewrite the component**

Replace the full contents of `FrontEnd/src/components/BacktestChart.jsx` with:

```jsx
import React from 'react';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ComposedChart
} from 'recharts';

function formatDateTick(value) {
  return typeof value === 'string' ? value.split('T')[0] : value;
}

export default function BacktestChart({
  data, trades, equityCurve, benchmarkEquityCurve = [], priceName = 'Stock Price', equityName = 'Equity',
}) {
  const entryByDate = new Map();
  const exitByDate = new Map();
  (trades || []).forEach((t) => {
    if (t.type === 'entry') entryByDate.set(t.date, t);
    else if (t.type === 'exit') exitByDate.set(t.date, t);
  });

  const chartData = (data || []).map((d) => {
    const entry = entryByDate.get(d.date) || null;
    const exit = exitByDate.get(d.date) || null;
    const isShortEntry = entry && entry.direction === 'short';
    const isShortExit = exit && exit.direction === 'short';
    return {
      date: d.date,
      close: d.close,
      longEntry: entry && !isShortEntry ? entry.price : null,
      shortEntry: isShortEntry ? entry.price : null,
      longExit: exit && !isShortExit ? exit.price : null,
      shortExit: isShortExit ? exit.price : null,
      pnl: exit ? exit.pnl : null,
    };
  });

  const benchmarkByDate = new Map((benchmarkEquityCurve || []).map((p) => [p.date, p.equity]));
  const hasBenchmark = (benchmarkEquityCurve || []).length > 0;
  const equityData = (equityCurve || []).map((point) => ({
    date: point.date,
    equity: point.equity,
    benchmark: benchmarkByDate.has(point.date) ? benchmarkByDate.get(point.date) : null,
  }));

  if (chartData.length === 0 && equityData.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  return (
    <div className="chart-container">
      {chartData.length > 0 && (
        <ResponsiveContainer width="100%" height={400}>
          <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDateTick}
              tick={{ fontSize: 12 }}
              interval={Math.floor(chartData.length / 10)}
            />
            <YAxis yAxisId="left" />
            <YAxis yAxisId="right" orientation="right" />
            <Tooltip
              formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
              labelFormatter={formatDateTick}
              contentStyle={{ backgroundColor: '#10141f', border: '1px solid rgba(255, 255, 255, 0.08)' }}
              labelStyle={{ color: '#f5f7fb' }}
            />
            <Legend />

            <Line
              yAxisId="left"
              type="monotone"
              dataKey="close"
              stroke="#5da2ff"
              name={priceName}
              dot={false}
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="longEntry"
              fill="#7cf2d4"
              name="Long Entry"
              shape="triangle"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="shortEntry"
              fill="#ffb86c"
              name="Short Entry"
              shape="wye"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="longExit"
              fill="#ff6b6b"
              name="Long Exit"
              shape="diamond"
              isAnimationActive={false}
            />

            <Scatter
              yAxisId="left"
              dataKey="shortExit"
              fill="#ff4da6"
              name="Short Exit"
              shape="square"
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      )}

      {equityData.length > 0 && (
        <ResponsiveContainer width="100%" height={230}>
          <LineChart data={equityData} margin={{ top: 5, right: 30, left: 0, bottom: 25 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tickFormatter={formatDateTick}
              tick={{ fontSize: 12 }}
              interval={Math.floor(equityData.length / 10)}
            />
            <YAxis />
            <Tooltip
              formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
              labelFormatter={formatDateTick}
              contentStyle={{ backgroundColor: '#10141f', border: '1px solid rgba(255, 255, 255, 0.08)' }}
              labelStyle={{ color: '#f5f7fb' }}
            />
            <Legend />
            <Line
              type="monotone"
              dataKey="equity"
              stroke="#c792ea"
              name={equityName}
              dot={false}
              isAnimationActive={false}
            />
            {hasBenchmark && (
              <Line
                type="monotone"
                dataKey="benchmark"
                stroke="#5da2ff"
                strokeDasharray="4 4"
                name="Buy & Hold"
                dot={false}
                isAnimationActive={false}
                connectNulls
              />
            )}
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
cd FrontEnd
git add src/components/BacktestChart.jsx
git commit -m "Add benchmark overlay and equity-only rendering to BacktestChart"
```

---

### Task 12: `StrategiesPage.jsx` — Walk-forward mode toggle and example strategy

**Files:**
- Modify: `FrontEnd/src/pages/StrategiesPage.jsx`

**Interfaces:**
- Consumes: `backtestApi.runWalkForwardBacktest` (Task 10).

- [ ] **Step 1: Add walk-forward mode state**

After the existing `portfolioRows` state declaration (lines 49-52), add:

```javascript
  const [testWindowMonths, setTestWindowMonths] = useState(6);
```

- [ ] **Step 2: Derive whether the selected strategy is custom-code**

After the `filtered` `useMemo` block (lines 115-117), add:

```javascript
  const selectedStrategyConfig = useMemo(() => {
    const strategy = filtered.find((s) => s.id === selectedStrategyId);
    if (!strategy) return null;
    try {
      return typeof strategy.parameters === 'string' ? JSON.parse(strategy.parameters) : strategy.parameters;
    } catch {
      return null;
    }
  }, [filtered, selectedStrategyId]);
  const isCustomCodeStrategy = selectedStrategyConfig?.mode === 'custom_code';

  useEffect(() => {
    if (backtestMode === 'walk_forward' && !isCustomCodeStrategy) {
      setBacktestMode('single');
    }
  }, [isCustomCodeStrategy, backtestMode]);
```

- [ ] **Step 3: Branch `handleRunBacktest` on the third mode**

Replace the existing `handleRunBacktest` function (lines 154-199) with:

```javascript
  const handleRunBacktest = async (e) => {
    e.preventDefault();
    if (!selectedStrategyId) {
      setError('Please select a strategy first');
      return;
    }
    setBacktestLoading(true);
    setError('');
    try {
      const stopLossPct = backtestForm.stopLossPct === '' ? null : Number(backtestForm.stopLossPct);
      const takeProfitPct = backtestForm.takeProfitPct === '' ? null : Number(backtestForm.takeProfitPct);
      if (backtestMode === 'portfolio') {
        await backtestApi.runPortfolioBacktest(
          selectedStrategyId,
          validPortfolioRows.map((r) => ({ ticker: r.ticker, weight: Number(r.weight) })),
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital,
          0.1,
          0.05,
          backtestForm.allowShort,
          stopLossPct,
          takeProfitPct
        );
      } else if (backtestMode === 'walk_forward') {
        await backtestApi.runWalkForwardBacktest(
          selectedStrategyId,
          backtestForm.ticker,
          backtestForm.startDate,
          backtestForm.endDate,
          testWindowMonths * 30, // approximate months->days; test_window_days is the API's unit
          backtestForm.initialCapital,
          0.1,
          0.05,
          backtestForm.allowShort,
          stopLossPct,
          takeProfitPct
        );
      } else {
        await backtestApi.runBacktest(
          selectedStrategyId,
          backtestForm.ticker,
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital,
          0.1,
          0.05,
          backtestForm.allowShort,
          stopLossPct,
          takeProfitPct
        );
      }
      // Redirect to results page
      window.location.href = `/strategies/${selectedStrategyId}/backtest`;
    } catch (err) {
      setError(err?.response?.data?.detail || 'Backtest failed');
    } finally {
      setBacktestLoading(false);
    }
  };
```

- [ ] **Step 4: Add the third toggle button**

Replace the mode-toggle button group (lines 274-289) with:

```jsx
              <div className="title-row" style={{ marginBottom: '8px' }}>
                <button
                  type="button"
                  className={backtestMode === 'single' ? 'primary-btn' : 'ghost-btn'}
                  onClick={() => setBacktestMode('single')}
                >
                  Single ticker
                </button>
                <button
                  type="button"
                  className={backtestMode === 'portfolio' ? 'primary-btn' : 'ghost-btn'}
                  onClick={() => setBacktestMode('portfolio')}
                >
                  Portfolio
                </button>
                <button
                  type="button"
                  className={backtestMode === 'walk_forward' ? 'primary-btn' : 'ghost-btn'}
                  onClick={() => setBacktestMode('walk_forward')}
                  disabled={!isCustomCodeStrategy}
                  title={isCustomCodeStrategy ? undefined : 'Walk-forward evaluation requires a custom-code (Python) strategy'}
                >
                  Walk-forward
                </button>
              </div>
```

- [ ] **Step 5: Show the ticker field and date-range constraints for walk-forward mode too**

Change the ticker field's condition (line 305) from:

```jsx
                {backtestMode === 'single' && (
```

to:

```jsx
                {(backtestMode === 'single' || backtestMode === 'walk_forward') && (
```

Change the Start Date and End Date fields' `min`/`max` conditions (lines 336-337 and 346-347) from `backtestMode === 'single'` to `(backtestMode === 'single' || backtestMode === 'walk_forward')` in all four occurrences.

- [ ] **Step 6: Add the test-window field and example-strategy panel**

After the portfolio-rows block's closing `)}` (after line 437), add:

```jsx
              {backtestMode === 'walk_forward' && (
                <div className="stack" style={{ marginTop: '8px' }}>
                  <label className="field">
                    <span>Test window length</span>
                    <select
                      value={testWindowMonths}
                      onChange={(e) => setTestWindowMonths(Number(e.target.value))}
                    >
                      <option value={3}>3 months</option>
                      <option value={6}>6 months</option>
                      <option value={12}>12 months</option>
                    </select>
                  </label>
                  <details>
                    <summary>Example strategy for walk-forward</summary>
                    <pre className="code-editor" style={{ whiteSpace: 'pre-wrap' }}>
{`from sklearn.linear_model import LogisticRegression

def generate_signals(df):
    df = df.copy()
    df['return_1d'] = df['close'].pct_change()
    df['sma_10'] = df['close'].rolling(10).mean()
    df['sma_30'] = df['close'].rolling(30).mean()
    df['momentum'] = df['close'] / df['close'].shift(10) - 1
    df['target'] = (df['close'].shift(-1) > df['close']).astype(int)

    features = ['return_1d', 'sma_10', 'sma_30', 'momentum']
    train = df.dropna(subset=features + ['target'])

    # Walk-forward calls this fresh each fold with a growing df --
    # early folds may not have enough rows yet. Stay flat rather than
    # fit on too little data.
    if len(train) < 50:
        return df['close'] * 0

    model = LogisticRegression()
    model.fit(train[features], train['target'])

    predictable = df.dropna(subset=features)
    preds = model.predict(predictable[features])  # 0/1

    signal = df['close'] * 0
    signal.loc[predictable.index] = preds * 2 - 1  # -> -1/1
    return signal`}
                    </pre>
                  </details>
                </div>
              )}
```

- [ ] **Step 7: Simplify the submit-button disabled condition**

Replace the submit button's `disabled` prop (lines 442-446) with:

```jsx
                disabled={
                  backtestLoading ||
                  !selectedStrategyId ||
                  (backtestMode === 'portfolio' ? validPortfolioRows.length < 2 : !backtestForm.ticker)
                }
```

- [ ] **Step 8: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 9: Commit**

```bash
cd FrontEnd
git add src/pages/StrategiesPage.jsx
git commit -m "Add walk-forward backtest mode with example strategy to StrategiesPage"
```

---

### Task 13: `BacktestResultsPage.jsx` — merged results, progress, per-fold breakdown

**Files:**
- Modify: `FrontEnd/src/pages/BacktestResultsPage.jsx` (full-file rewrite — three-way type branching touches most of the file)

**Interfaces:**
- Consumes: `backtestApi.getWalkForwardBacktestResults`/`getWalkForwardBacktestDetail` (Task 10), `BacktestChart`'s `benchmarkEquityCurve`/`equityName` props (Task 11).

- [ ] **Step 1: Rewrite the component**

Replace the full contents of `FrontEnd/src/pages/BacktestResultsPage.jsx` with:

```jsx
import React, { useEffect, useRef, useState } from 'react';
import { useParams } from 'react-router-dom';
import BacktestChart from '../components/BacktestChart.jsx';
import * as backtestApi from '../api/backtest.js';

export default function BacktestResultsPage() {
  const { strategyId } = useParams();
  const [results, setResults] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedResult, setSelectedResult] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [expandedTicker, setExpandedTicker] = useState(null);

  const selectedResultRef = useRef(null);
  useEffect(() => {
    selectedResultRef.current = selectedResult;
  }, [selectedResult]);

  const loadResults = async ({ preserveSelection = false } = {}) => {
    if (!preserveSelection) setLoading(true);
    setError('');
    try {
      const [singleResults, portfolioResults, walkForwardResults] = await Promise.all([
        backtestApi.getBacktestResults(strategyId),
        backtestApi.getPortfolioBacktestResults(strategyId),
        backtestApi.getWalkForwardBacktestResults(strategyId),
      ]);
      const merged = [
        ...(singleResults || []).map((r) => ({ ...r, _type: 'single' })),
        ...(portfolioResults || []).map((r) => ({ ...r, _type: 'portfolio' })),
        ...(walkForwardResults || []).map((r) => ({ ...r, _type: 'walk_forward' })),
      ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      setResults(merged);
      if (!preserveSelection && merged.length > 0) {
        loadDetail(merged[0]);
      } else if (preserveSelection && selectedResultRef.current) {
        const prev = selectedResultRef.current;
        const updated = merged.find((r) => r.id === prev.id && r._type === prev._type);
        const finished = updated && updated.status !== prev.status && (updated.status === 'success' || updated.status === 'failed');
        const progressed = updated && updated._type === 'walk_forward' && updated.folds_completed !== prev.folds_completed;
        if (finished || progressed) {
          loadDetail(updated);
        }
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest results');
    } finally {
      if (!preserveSelection) setLoading(false);
    }
  };

  const loadDetail = async (result) => {
    setDetailLoading(true);
    setExpandedTicker(null);
    try {
      let data;
      if (result._type === 'portfolio') {
        data = await backtestApi.getPortfolioBacktestDetail(result.id);
      } else if (result._type === 'walk_forward') {
        data = await backtestApi.getWalkForwardBacktestDetail(result.id);
      } else {
        data = await backtestApi.getBacktestDetail(result.id);
      }
      setSelectedResult({ ...data, _type: result._type });
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest details');
    } finally {
      setDetailLoading(false);
    }
  };

  useEffect(() => {
    loadResults();
  }, [strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const active = results.some((r) => r.status === 'pending' || r.status === 'running');
    if (!active) return undefined;
    const timer = setInterval(() => {
      loadResults({ preserveSelection: true });
    }, 2500);
    return () => clearInterval(timer);
  }, [results, strategyId]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="page">
      <div className="page-head">
        <p className="pill">Strategy {strategyId}</p>
        <h1>Backtest Results</h1>
      </div>

      <div className="layout two-cols">
        {/* Results List */}
        <div className="card">
          <div className="card-head">
            <h3>Previous backtests</h3>
            <button className="ghost-btn" onClick={loadResults}>Refresh</button>
          </div>
          {loading && <p>Loading...</p>}
          {error && <div className="error-box">{error}</div>}
          {!loading && results.length === 0 && <p className="muted">No backtest results yet.</p>}
          <div className="list">
            {results.map((result) => (
              <div
                key={`${result._type}-${result.id}`}
                className={`list-row ${selectedResult?.id === result.id && selectedResult?._type === result._type ? 'active' : ''}`}
                onClick={() => loadDetail(result)}
                style={{ cursor: 'pointer' }}
              >
                <div>
                  <div className="title-row">
                    <span className="title">Backtest #{result.id}</span>
                    {result._type === 'portfolio' && (
                      <span className="chip">Portfolio · {result.allocations?.length || 0} tickers</span>
                    )}
                    {result._type === 'walk_forward' && (
                      <span className="chip">
                        Walk-forward · {result.status === 'running'
                          ? `Fold ${result.folds_completed || 0}/${result.total_folds || '?'}`
                          : `${result.total_folds || 0} folds`}
                      </span>
                    )}
                    {result._type === 'single' && (
                      <span className="chip">{result.num_trades} trades</span>
                    )}
                    {result.status && result.status !== 'success' && (
                      <span className="chip">{result.status}</span>
                    )}
                  </div>
                  <p className="muted">{result.created_at}</p>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Details */}
        <div className="card">
          {detailLoading && <p>Loading details...</p>}
          {selectedResult && (
            <>
              <h3>Performance Metrics</h3>
              {selectedResult.status && selectedResult.status !== 'success' && (
                <div
                  className={selectedResult.status === 'failed' ? 'error-box' : 'muted'}
                  style={{ marginBottom: '12px' }}
                >
                  {selectedResult.status === 'failed'
                    ? `Backtest failed: ${selectedResult.error_message || 'Unknown error'}`
                    : selectedResult._type === 'walk_forward'
                      ? `Running fold ${selectedResult.folds_completed || 0} of ${selectedResult.total_folds || '?'}…`
                      : `Backtest ${selectedResult.status}…`}
                </div>
              )}
              <div className="metrics-grid">
                <div className="metric">
                  <span className="label">Total Return</span>
                  <span className="value">${selectedResult.metrics?.total_return?.toFixed(2) || 0}</span>
                </div>
                <div className="metric">
                  <span className="label">Return %</span>
                  <span className="value">{selectedResult.metrics?.return_pct?.toFixed(2) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Win Rate</span>
                  <span className="value">{selectedResult.metrics?.win_rate?.toFixed(1) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Trades</span>
                  <span className="value">{selectedResult.metrics?.num_trades || 0}</span>
                </div>
                <div className="metric">
                  <span className="label">Max Drawdown</span>
                  <span className="value">{selectedResult.metrics?.max_drawdown_pct?.toFixed(2) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Sharpe Ratio</span>
                  <span className="value">{selectedResult.metrics?.sharpe_ratio?.toFixed(2) || 0}</span>
                </div>
              </div>

              {selectedResult._type === 'portfolio' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Per-ticker breakdown</h3>
                  <div className="list">
                    {(selectedResult.allocations || []).map((alloc) => {
                      const tickerResult = selectedResult.per_ticker?.[alloc.ticker];
                      return (
                        <div key={alloc.ticker}>
                          <div
                            className="list-row"
                            onClick={() => setExpandedTicker(expandedTicker === alloc.ticker ? null : alloc.ticker)}
                            style={{ cursor: 'pointer' }}
                          >
                            <div>
                              <div className="title-row">
                                <span className="title">{alloc.ticker}</span>
                                <span className="chip">{(alloc.weight * 100).toFixed(1)}% weight</span>
                              </div>
                              <p className="muted">
                                Return {tickerResult?.metrics?.return_pct?.toFixed(2) || 0}%
                                {' · '}
                                {tickerResult?.metrics?.num_trades || 0} trades
                                {' · '}
                                Win rate {tickerResult?.metrics?.win_rate?.toFixed(1) || 0}%
                              </p>
                            </div>
                          </div>
                          {expandedTicker === alloc.ticker && tickerResult && (
                            <BacktestChart
                              data={tickerResult.signals}
                              trades={tickerResult.trades}
                              equityCurve={tickerResult.equity_curve}
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                </>
              )}

              {selectedResult._type === 'walk_forward' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Per-fold breakdown</h3>
                  <div className="trades-list">
                    {(selectedResult.folds || []).map((fold) => (
                      <div key={fold.fold_index} className="trade-item">
                        <span className="badge">Fold {fold.fold_index + 1}</span>
                        <span className="muted">
                          Train {fold.train_start.slice(0, 10)} → {fold.train_end.slice(0, 10)}
                        </span>
                        <span className="muted">
                          Test {fold.test_start.slice(0, 10)} → {fold.test_end.slice(0, 10)}
                        </span>
                        <span className={fold.return_pct > 0 ? 'profit' : 'loss'}>
                          {fold.return_pct > 0 ? '+' : ''}{fold.return_pct.toFixed(2)}%
                        </span>
                        <span className="muted">{fold.num_trades} trades</span>
                      </div>
                    ))}
                  </div>
                </>
              )}

              {selectedResult._type === 'single' && (
                <>
                  <h3 style={{ marginTop: '20px' }}>Trades</h3>
                  <div className="trades-list">
                    {selectedResult.trades && selectedResult.trades.map((trade, idx) => (
                      <div key={idx} className="trade-item">
                        <span className={`badge ${trade.type === 'entry' ? 'entry' : 'exit'}`}>
                          {trade.type.toUpperCase()}
                        </span>
                        <span className={`badge direction-${trade.direction === 'short' ? 'short' : 'long'}`}>
                          {trade.direction === 'short' ? 'SHORT' : 'LONG'}
                        </span>
                        <span>${trade.price.toFixed(2)}</span>
                        <span className="muted">{trade.date}</span>
                        {trade.exit_reason && trade.exit_reason !== 'signal' && (
                          <span className="muted">
                            {trade.exit_reason === 'stop_loss' ? 'Stopped out' : 'Took profit'}
                          </span>
                        )}
                        {trade.pnl && <span className={trade.pnl > 0 ? 'profit' : 'loss'}>
                          {trade.pnl > 0 ? '+' : ''}{trade.pnl.toFixed(2)}
                        </span>}
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      </div>

      {/* Aggregate chart */}
      {selectedResult && selectedResult._type === 'single' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals}
            trades={selectedResult.trades}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'portfolio' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Portfolio Equity</h3>
          <BacktestChart
            data={[]}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
            equityName="Portfolio Equity"
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'walk_forward' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Walk-Forward OOS Equity</h3>
          <BacktestChart
            data={[]}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
            benchmarkEquityCurve={selectedResult.benchmark_equity_curve}
            equityName="Walk-Forward OOS Equity"
          />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
cd FrontEnd
git add src/pages/BacktestResultsPage.jsx
git commit -m "Merge walk-forward results into BacktestResultsPage with progress and per-fold breakdown"
```

---

### Task 14: Manual end-to-end QA

**Files:** none (verification only)

- [ ] **Step 1: Start the stack**

From the repo root: `docker compose up --build` (or run backend/frontend dev servers separately per CLAUDE.md's Commands section). Since there's no migration tooling, if this is an existing dev DB, drop and let `init_db()` recreate it (or run against a fresh Postgres volume) so the new `walk_forward_backtest_results` table and the `benchmark_equity_curve` columns on the two existing result tables exist.

- [ ] **Step 2: Seed enough market data for multiple folds**

Log in, go to `/data`, upload or import at least ~5 years of daily bars for one ticker (e.g. via CSV upload) — enough to clear the 365-day minimum train window plus several 6-month test folds.

- [ ] **Step 3: Create a custom-code strategy and confirm the toggle gating**

Go to a project's Strategies page, create a rules-based strategy first — confirm its backtest form's "Walk-forward" toggle is disabled with an explanatory tooltip. Then create a custom-code strategy (paste the example strategy shown in the walk-forward panel, or your own `generate_signals`), select it, and confirm "Walk-forward" becomes enabled.

- [ ] **Step 4: Run a walk-forward backtest and watch progress**

Switch to "Walk-forward" mode, pick the ticker, a multi-year date range, and a 6-month test window; submit. On the results page, confirm the list badge shows `Fold N/M` while running and updates as folds complete, without a full page reload (2.5s polling).

- [ ] **Step 5: Verify the completed result**

Once `status` is `success`, confirm: aggregate metrics render in the metrics grid, the per-fold breakdown table lists every fold with train/test ranges and a return %, and the "Walk-Forward OOS Equity" chart shows both the strategy equity line and a dashed "Buy & Hold" benchmark line.

- [ ] **Step 6: Verify the benchmark overlay on the other two result types**

Run (or open an existing) single-ticker backtest and a portfolio backtest for the same strategy/tickers; confirm both now show a dashed "Buy & Hold" line in their equity panel too.

- [ ] **Step 7: Verify the error paths**

Submit a walk-forward backtest with a date range too short to fit even one fold (e.g. 3 months); confirm the row ends up `status="failed"` with a "date range too short" message, not a generic error. Then try the API directly (or via the UI, if reachable) against a rules-mode strategy's ID to confirm the 400 "requires a custom-code strategy" rejection.

- [ ] **Step 8: Confirm existing flows are unaffected**

Switch back to "Single ticker" and "Portfolio" modes and run a normal backtest of each, confirming both existing flows still work end-to-end.

# Portfolio-Level Backtests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "Portfolio" backtest mode that runs one strategy independently across a basket of tickers, each funded from a custom fixed weight of initial capital, producing an aggregate portfolio equity curve/metrics plus a per-ticker breakdown — without touching the existing single-ticker backtest path.

**Architecture:** A new `services/portfolio_backtest_service.py` orchestrates: normalize ticker weights, validate each ticker has full date-range coverage, run each ticker through the existing unmodified `StrategyExecutor.backtest()` against its own capital sub-account, then aggregate the per-ticker equity curves (date-union with forward-fill) and pooled metrics. Results persist to a new `PortfolioBacktestResult` table. New router endpoints under `/api/backtest/`. Frontend adds a Single/Portfolio toggle to the existing backtest form and merges portfolio results into the existing results page.

**Tech Stack:** FastAPI, SQLAlchemy (async, `sqlite+aiosqlite` in tests / Postgres in prod), pandas, pytest/pytest-asyncio, React, recharts (existing `BacktestChart.jsx`, unmodified).

## Global Constraints

- Basket diversification only — no rebalancing, no cross-ticker conditions, no shorting. Weights are applied once at backtest start.
- Weights auto-normalize to sum to 1.0; the caller does not need to supply values that already sum to 100.
- Portfolio backtests require ≥2 tickers with strictly positive weights.
- Every ticker must have data spanning the full requested `[start_date, end_date]` range or the whole request is rejected with a 400 naming the ticker and its actual range — no silent partial-data runs.
- This is a new mode alongside the existing single-ticker backtest: `BacktestResult`, `routers/backtest.py`'s existing `run`/`results`/`{backtest_id}` endpoints, `services/backtest_service.py`, and `StrategyExecutor.backtest()`'s public behavior must not change.
- No migration tooling exists (`init_db()` only runs `Base.metadata.create_all`) — after the schema change in Task 1, the local dev DB's tables need to be dropped and recreated manually.
- All new async DB code follows the existing eager-loading pattern (`selectinload`) for any `.project`/`.strategy` relationship walk, to avoid the documented `MissingGreenlet` regression.
- Backend tests run from `BackEnd/` via `uv run pytest tests/ -v`. No mocks — use a real in-memory `sqlite+aiosqlite` engine (`pytest-asyncio`/`aiosqlite`, already dev deps) for anything DB-touching, matching `tests/test_backtest_ownership.py`'s style.
- Frontend has no test suite (`npm run lint` is a no-op) — frontend tasks verify with `npm run build` (catches syntax/import errors) plus a final manual QA pass in a real browser per CLAUDE.md's UI-change guidance.

---

### Task 1: `PortfolioBacktestResult` data model

**Files:**
- Modify: `BackEnd/database/models.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (new file — later tasks append to it)

**Interfaces:**
- Produces: `PortfolioBacktestResult` ORM class with columns `id, strategy_id, start_date, end_date, initial_capital, commission_pct, slippage_pct, allocations (JSON), results (JSON), equity_curve (JSON), per_ticker (JSON), created_at`, and relationship `strategy` (back-populated from `Strategy.portfolio_backtests`).

- [ ] **Step 1: Write the failing test**

Create `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
"""Tests for portfolio-level backtests: basket diversification across
multiple tickers with custom fixed weights, aggregated into one portfolio
equity curve/metrics on top of the existing single-ticker StrategyExecutor.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, PortfolioBacktestResult


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="strat", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        pbr = PortfolioBacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            allocations=[{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}],
            results={"return_pct": 1.0},
            equity_curve=[{"date": "2024-01-01T00:00:00", "equity": 10000.0}],
            per_ticker={"AAPL": {"metrics": {}}},
        )
        db.add(pbr)
        await db.commit()
        await db.refresh(pbr)

        result = await db.execute(
            select(PortfolioBacktestResult).where(PortfolioBacktestResult.id == pbr.id)
        )
        loaded = result.scalars().first()
        assert loaded.allocations == [
            {"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}
        ]
        assert loaded.initial_capital == 10000.0
        assert loaded.commission_pct == 0.1
        assert loaded.per_ticker == {"AAPL": {"metrics": {}}}
```

- [ ] **Step 2: Run test to verify it fails**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'PortfolioBacktestResult'`

- [ ] **Step 3: Add the model**

In `BackEnd/database/models.py`, change the import line at the top:

```python
from sqlalchemy import (
    Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, Index, Boolean, JSON, Float
)
```

Add a `portfolio_backtests` relationship to the existing `Strategy` class (alongside its existing `backtests` relationship line):

```python
    backtests = relationship("BacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    portfolio_backtests = relationship("PortfolioBacktestResult", back_populates="strategy", cascade="all, delete-orphan")
```

Add a new class after `BacktestResult`:

```python
class PortfolioBacktestResult(Base):
    __tablename__ = "portfolio_backtest_results"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    strategy = relationship("Strategy", back_populates="portfolio_backtests")
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    initial_capital = Column(Float, nullable=False)
    commission_pct = Column(Float, nullable=False)
    slippage_pct = Column(Float, nullable=False)
    allocations = Column(JSON, default=[])   # [{ticker, weight}] — normalized weights actually used
    results = Column(JSON, default={})       # aggregate portfolio metrics
    equity_curve = Column(JSON, default=[])  # aggregate portfolio {date, equity} series
    per_ticker = Column(JSON, default={})    # {ticker: {allocated_capital, metrics, trades, signals, equity_curve}}
    created_at = Column(DateTime, default=datetime.utcnow)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add database/models.py tests/test_portfolio_backtest_service.py
git commit -m "Add PortfolioBacktestResult data model"
```

---

### Task 2: Extract drawdown/Sharpe as reusable module-level functions

**Files:**
- Modify: `BackEnd/services/strategy_executor.py:269-329`
- Test: `BackEnd/tests/test_strategy_executor.py` (existing suite must stay green — this task adds no new test file, it's a pure refactor verified by the existing suite)

**Interfaces:**
- Produces: module-level `max_drawdown_pct(equity_curve: List[Dict]) -> float` and `sharpe_ratio(equity_curve: List[Dict]) -> float` in `services/strategy_executor.py`, importable by `services/portfolio_backtest_service.py` (Task 6).
- Consumes: nothing new — this is the exact existing logic from `StrategyExecutor._max_drawdown_pct`/`._sharpe_ratio`, moved out of the class.

- [ ] **Step 1: Run the existing suite to establish the baseline**

Run (from `BackEnd/`): `uv run pytest tests/test_strategy_executor.py -v`
Expected: All tests PASS (this is the baseline before refactoring — confirms nothing is broken yet).

- [ ] **Step 2: Extract the functions**

In `BackEnd/services/strategy_executor.py`, replace lines 269-329 (the `_calculate_metrics`, `_max_drawdown_pct`, `_sharpe_ratio` methods) with:

```python
    def _calculate_metrics(self, df: pd.DataFrame, trades: List[Dict],
                            initial_capital: float, equity_curve: List[Dict]) -> Dict[str, Any]:
        """Calculate performance metrics"""
        max_dd = max_drawdown_pct(equity_curve)
        sharpe = sharpe_ratio(equity_curve)

        if not trades:
            return {
                'total_return': 0.0,
                'return_pct': 0.0,
                'win_rate': 0.0,
                'num_trades': 0,
                'max_drawdown_pct': max_dd,
                'sharpe_ratio': sharpe,
            }

        total_pnl = sum(t.get('pnl', 0) for t in trades if t['type'] == 'exit')

        if equity_curve:
            final_capital = equity_curve[-1]['equity']
        else:
            final_capital = initial_capital + total_pnl
        total_return = final_capital - initial_capital
        return_pct = (total_return / initial_capital) * 100

        exits = [t for t in trades if t['type'] == 'exit']
        wins = len([t for t in exits if t.get('pnl', 0) > 0])
        win_rate = (wins / len(exits) * 100) if exits else 0.0

        return {
            'total_return': float(total_return),
            'return_pct': float(return_pct),
            'win_rate': float(win_rate),
            'num_trades': len(exits),
            'final_capital': float(final_capital),
            'max_drawdown_pct': max_dd,
            'sharpe_ratio': sharpe,
        }


def max_drawdown_pct(equity_curve: List[Dict]) -> float:
    if not equity_curve:
        return 0.0
    peak = equity_curve[0]['equity']
    max_dd = 0.0
    for point in equity_curve:
        equity = point['equity']
        peak = max(peak, equity)
        if peak > 0:
            drawdown = (peak - equity) / peak * 100
            max_dd = max(max_dd, drawdown)
    return float(max_dd)


def sharpe_ratio(equity_curve: List[Dict]) -> float:
    if len(equity_curve) < 2:
        return 0.0
    values = pd.Series([p['equity'] for p in equity_curve])
    returns = values.pct_change().dropna()
    std = returns.std()
    if not std or pd.isna(std) or std == 0:
        return 0.0
    return float((returns.mean() / std) * (252 ** 0.5))
```

Note the two new functions are defined at module level (outside the `StrategyExecutor` class, dedented) — they become the last things in the file.

- [ ] **Step 3: Run tests to verify no regression**

Run: `uv run pytest tests/test_strategy_executor.py -v`
Expected: All tests PASS (same set as Step 1 — this confirms the refactor is behavior-preserving; nothing in the existing suite calls `_max_drawdown_pct`/`_sharpe_ratio` directly, only `_calculate_metrics`, so removing the instance methods is safe).

- [ ] **Step 4: Commit**

```bash
cd BackEnd
git add services/strategy_executor.py
git commit -m "Extract drawdown/Sharpe calculation as reusable module-level functions"
```

---

### Task 3: `normalize_weights` — ticker/weight validation

**Files:**
- Create: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `normalize_weights(tickers: List[Dict[str, Any]]) -> List[Dict[str, Any]]`. Input/output shape: `[{"ticker": str, "weight": float}, ...]`. Raises `ValueError` on invalid input. Later tasks (6, 7) import this from `services.portfolio_backtest_service`.

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from services.portfolio_backtest_service import normalize_weights


def test_normalize_weights_scales_to_sum_one():
    result = normalize_weights([{"ticker": "AAPL", "weight": 2}, {"ticker": "MSFT", "weight": 1}])
    assert result[0] == {"ticker": "AAPL", "weight": pytest.approx(2 / 3)}
    assert result[1] == {"ticker": "MSFT", "weight": pytest.approx(1 / 3)}


def test_normalize_weights_already_summing_to_one_is_unchanged():
    result = normalize_weights([{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}])
    assert result == [{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}]


def test_normalize_weights_rejects_single_ticker():
    with pytest.raises(ValueError, match="at least 2 tickers"):
        normalize_weights([{"ticker": "AAPL", "weight": 1}])


def test_normalize_weights_rejects_non_positive_weight():
    with pytest.raises(ValueError, match="AAPL"):
        normalize_weights([{"ticker": "AAPL", "weight": 0}, {"ticker": "MSFT", "weight": 1}])


def test_normalize_weights_rejects_duplicate_ticker():
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_weights([{"ticker": "AAPL", "weight": 1}, {"ticker": "AAPL", "weight": 1}])
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k normalize_weights`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.portfolio_backtest_service'`

- [ ] **Step 3: Create the service file with `normalize_weights`**

Create `BackEnd/services/portfolio_backtest_service.py`:

```python
"""Portfolio-level backtests: run one strategy independently across a
basket of tickers, each funded from a custom fixed weight of the initial
capital, and aggregate the results into one portfolio equity curve/metrics.

Each ticker's sub-account runs through the existing, unmodified
StrategyExecutor.backtest() — this module only validates the basket,
allocates capital, and aggregates the per-ticker results. There is no
rebalancing and no cross-ticker strategy logic (see the design spec at
docs/superpowers/specs/2026-08-13-portfolio-backtests-design.md).
"""
from datetime import datetime
from typing import Any, Dict, List

from typing import Any, Dict, List


def normalize_weights(tickers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Validate a portfolio ticker list and normalize weights to sum to 1.0.

    Args:
        tickers: [{"ticker": str, "weight": float}, ...]. Weights need not
            already sum to 1.0 or 100 — e.g. {2, 1} normalizes to {2/3, 1/3}.

    Raises:
        ValueError: fewer than 2 tickers, a non-positive weight, or a
            duplicate ticker.
    """
    if len(tickers) < 2:
        raise ValueError("Portfolio backtest requires at least 2 tickers with positive weights")

    seen = set()
    for t in tickers:
        if t["weight"] <= 0:
            raise ValueError(f"Weight for {t['ticker']} must be positive")
        if t["ticker"] in seen:
            raise ValueError(f"Duplicate ticker in portfolio: {t['ticker']}")
        seen.add(t["ticker"])

    total = sum(t["weight"] for t in tickers)
    return [{"ticker": t["ticker"], "weight": t["weight"] / total} for t in tickers]
```

(Note: the duplicate `from typing import ...` line will be cleaned up as later steps in this task add more imports — leave as-is for now, it's harmless.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k normalize_weights`
Expected: PASS (5 tests)

- [ ] **Step 5: Remove the duplicate import line**

Edit `BackEnd/services/portfolio_backtest_service.py`: delete the second `from typing import Any, Dict, List` line so only one remains.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add normalize_weights for portfolio ticker/weight validation"
```

---

### Task 4: `_check_ticker_coverage` — full date-range validation

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `async def _check_ticker_coverage(ticker: str, start_dt: datetime, end_dt: datetime, db: AsyncSession) -> None`. Raises `ValueError` if the ticker has no data, or its available range doesn't fully cover `[start_dt, end_dt]`. Consumed by Task 7's orchestrator.

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from database.models import MarketData
from services.portfolio_backtest_service import _check_ticker_coverage


async def _seed_market_data(db, ticker, dates_and_closes):
    for d, close in dates_and_closes:
        db.add(MarketData(
            ticker=ticker, date=d, open=str(close), high=str(close),
            low=str(close), close=str(close), volume="1000",
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_check_ticker_coverage_passes_when_range_is_covered(session_factory):
    async with session_factory() as db:
        await _seed_market_data(db, "AAPL", [
            (datetime(2024, 1, 1), 10), (datetime(2024, 1, 10), 12),
        ])
        # Should not raise
        await _check_ticker_coverage("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 10), db)


@pytest.mark.asyncio
async def test_check_ticker_coverage_rejects_shorter_range(session_factory):
    async with session_factory() as db:
        await _seed_market_data(db, "AAPL", [
            (datetime(2024, 1, 3), 10), (datetime(2024, 1, 7), 12),
        ])
        with pytest.raises(ValueError, match="AAPL"):
            await _check_ticker_coverage("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 10), db)


@pytest.mark.asyncio
async def test_check_ticker_coverage_rejects_missing_ticker(session_factory):
    async with session_factory() as db:
        with pytest.raises(ValueError, match="MSFT"):
            await _check_ticker_coverage("MSFT", datetime(2024, 1, 1), datetime(2024, 1, 10), db)
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k check_ticker_coverage`
Expected: FAIL with `ImportError: cannot import name '_check_ticker_coverage'`

- [ ] **Step 3: Implement `_check_ticker_coverage`**

In `BackEnd/services/portfolio_backtest_service.py`, add these new import lines directly below the existing `from datetime import datetime` / `from typing import Any, Dict, List` lines (don't duplicate those two — only add the ones below that aren't already present):

```python
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import MarketData
```

Append the function after `normalize_weights`:

```python
async def _check_ticker_coverage(ticker: str, start_dt: datetime, end_dt: datetime, db: AsyncSession) -> None:
    """Raise ValueError unless `ticker` has data spanning the full
    [start_dt, end_dt] range — mirrors the range check GET /api/data/{ticker}/range
    already exposes, so a portfolio run fails fast with a clear message
    instead of silently aggregating over partial data."""
    result = await db.execute(
        select(
            func.min(MarketData.date), func.max(MarketData.date), func.count(MarketData.id)
        ).where(MarketData.ticker == ticker)
    )
    min_date, max_date, count = result.one()
    if not count:
        raise ValueError(f"No market data found for ticker '{ticker}'")
    if min_date > start_dt or max_date < end_dt:
        raise ValueError(
            f"{ticker} has data from {min_date.date()} to {max_date.date()}, "
            f"which does not cover the requested {start_dt.date()} to {end_dt.date()}"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k check_ticker_coverage`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add ticker date-range coverage validation for portfolio backtests"
```

---

### Task 5: `aggregate_equity_curves` — combine per-ticker curves

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `aggregate_equity_curves(per_ticker_curves: Dict[str, List[Dict]], allocated_capital: Dict[str, float]) -> List[Dict]`. Each input curve is a chronologically-ordered list of `{"date": <ISO str>, "equity": float}` (the exact shape `StrategyExecutor.backtest()` already returns as `equity_curve`). Output is one combined list of `{"date", "equity"}`, sorted by date, summed across tickers. Consumed by Task 7.

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from services.portfolio_backtest_service import aggregate_equity_curves


def test_aggregate_equity_curves_sums_matching_dates():
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}],
        "MSFT": [{"date": "d1", "equity": 200.0}, {"date": "d2", "equity": 190.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},
        {"date": "d2", "equity": 300.0},
    ]


def test_aggregate_equity_curves_forward_fills_missing_middle_date():
    # MSFT has no bar on "d2" (a bar-level gap) — its d1 value should carry forward
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}, {"date": "d3", "equity": 120.0}],
        "MSFT": [{"date": "d1", "equity": 200.0}, {"date": "d3", "equity": 205.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},
        {"date": "d2", "equity": 310.0},  # AAPL 110 + MSFT forward-filled 200
        {"date": "d3", "equity": 325.0},
    ]


def test_aggregate_equity_curves_uses_allocated_capital_before_first_point():
    # MSFT's curve starts at "d2" — "d1" should use its starting allocated_capital
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}],
        "MSFT": [{"date": "d2", "equity": 205.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},  # AAPL 100 + MSFT's uninvested 200
        {"date": "d2", "equity": 315.0},
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k aggregate_equity_curves`
Expected: FAIL with `ImportError: cannot import name 'aggregate_equity_curves'`

- [ ] **Step 3: Implement `aggregate_equity_curves`**

Append to `BackEnd/services/portfolio_backtest_service.py`:

```python
def aggregate_equity_curves(
    per_ticker_curves: Dict[str, List[Dict[str, Any]]],
    allocated_capital: Dict[str, float],
) -> List[Dict[str, Any]]:
    """Combine per-ticker equity curves (each a chronologically-ordered list
    of {'date', 'equity'} dicts) into one portfolio equity curve summed
    across tickers. Dates are the union across all tickers; a ticker with no
    entry for a given date uses its last-known equity (forward-fill), or its
    starting allocated_capital for any date before its first data point."""
    all_dates = sorted({point["date"] for curve in per_ticker_curves.values() for point in curve})
    lookups = {
        ticker: {p["date"]: p["equity"] for p in curve}
        for ticker, curve in per_ticker_curves.items()
    }

    last_known = dict(allocated_capital)
    portfolio_curve = []
    for date in all_dates:
        total = 0.0
        for ticker, lookup in lookups.items():
            if date in lookup:
                last_known[ticker] = lookup[date]
            total += last_known[ticker]
        portfolio_curve.append({"date": date, "equity": total})
    return portfolio_curve
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k aggregate_equity_curves`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add aggregate_equity_curves for portfolio equity aggregation"
```

---

### Task 6: `aggregate_portfolio_metrics` — pooled metrics

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `aggregate_portfolio_metrics(per_ticker_results: Dict[str, Dict], portfolio_equity_curve: List[Dict], initial_capital: float) -> Dict[str, Any]`. Each `per_ticker_results[ticker]` is the exact dict shape `StrategyExecutor.backtest()` returns (`{'trades', 'metrics', 'signals', 'equity_curve'}`). Returns `{'total_return', 'return_pct', 'final_capital', 'win_rate', 'num_trades', 'max_drawdown_pct', 'sharpe_ratio'}`. Consumed by Task 7.
- Consumes: `max_drawdown_pct`/`sharpe_ratio` from `services.strategy_executor` (Task 2).

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from services.portfolio_backtest_service import aggregate_portfolio_metrics


def test_aggregate_portfolio_metrics_pools_trades_across_tickers():
    per_ticker_results = {
        "AAPL": {"trades": [
            {"type": "entry", "price": 100, "date": "d1", "size": 1},
            {"type": "exit", "price": 110, "date": "d2", "size": 1, "pnl": 10.0},
        ]},
        "MSFT": {"trades": [
            {"type": "entry", "price": 200, "date": "d1", "size": 1},
            {"type": "exit", "price": 190, "date": "d2", "size": 1, "pnl": -10.0},
        ]},
    }
    portfolio_equity_curve = [
        {"date": "d1", "equity": 1000.0},
        {"date": "d2", "equity": 1000.0},
    ]
    metrics = aggregate_portfolio_metrics(per_ticker_results, portfolio_equity_curve, 1000.0)

    assert metrics["num_trades"] == 2
    assert metrics["win_rate"] == pytest.approx(50.0)
    assert metrics["final_capital"] == pytest.approx(1000.0)
    assert metrics["total_return"] == pytest.approx(0.0)
    assert metrics["return_pct"] == pytest.approx(0.0)
    assert "max_drawdown_pct" in metrics
    assert "sharpe_ratio" in metrics


def test_aggregate_portfolio_metrics_handles_no_trades():
    portfolio_equity_curve = [{"date": "d1", "equity": 1000.0}]
    metrics = aggregate_portfolio_metrics({"AAPL": {"trades": []}}, portfolio_equity_curve, 1000.0)
    assert metrics["num_trades"] == 0
    assert metrics["win_rate"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k aggregate_portfolio_metrics`
Expected: FAIL with `ImportError: cannot import name 'aggregate_portfolio_metrics'`

- [ ] **Step 3: Implement `aggregate_portfolio_metrics`**

Add to the imports at the top of `BackEnd/services/portfolio_backtest_service.py`:

```python
from services.strategy_executor import max_drawdown_pct, sharpe_ratio
```

Append the function:

```python
def aggregate_portfolio_metrics(
    per_ticker_results: Dict[str, Dict[str, Any]],
    portfolio_equity_curve: List[Dict[str, Any]],
    initial_capital: float,
) -> Dict[str, Any]:
    """Pool trades and equity across all tickers in the basket into one set
    of portfolio-level metrics, using the same field names as a single-ticker
    backtest's metrics so the frontend can render either with one component."""
    all_trades = [
        trade for result in per_ticker_results.values() for trade in result["trades"]
    ]

    final_capital = portfolio_equity_curve[-1]["equity"] if portfolio_equity_curve else initial_capital
    total_return = final_capital - initial_capital
    return_pct = (total_return / initial_capital) * 100 if initial_capital else 0.0

    exits = [t for t in all_trades if t["type"] == "exit"]
    wins = len([t for t in exits if t.get("pnl", 0) > 0])
    win_rate = (wins / len(exits) * 100) if exits else 0.0

    return {
        "total_return": float(total_return),
        "return_pct": float(return_pct),
        "final_capital": float(final_capital),
        "win_rate": float(win_rate),
        "num_trades": len(exits),
        "max_drawdown_pct": max_drawdown_pct(portfolio_equity_curve),
        "sharpe_ratio": sharpe_ratio(portfolio_equity_curve),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k aggregate_portfolio_metrics`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add aggregate_portfolio_metrics for pooled portfolio metrics"
```

---

### Task 7: `run_portfolio_backtest` orchestrator

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `async def run_portfolio_backtest(strategy_id: int, tickers: List[Dict], start_date: str, end_date: str, initial_capital: float, commission_pct: float, slippage_pct: float, db: AsyncSession, user: User) -> Dict[str, Any]`. Returns `{'id', 'strategy_id', 'allocations', 'metrics', 'equity_curve', 'per_ticker', 'created_at'}`. Consumed by Task 9's router endpoint.
- Consumes: `normalize_weights` (Task 3), `_check_ticker_coverage` (Task 4), `aggregate_equity_curves` (Task 5), `aggregate_portfolio_metrics` (Task 6), `StrategyExecutor` (existing, unmodified), `PortfolioBacktestResult` (Task 1).

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from services import portfolio_backtest_service


@pytest_asyncio.fixture
async def portfolio_seeded(session_factory):
    """User/project/strategy plus two tickers of clean, hand-verifiable price data."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="strat",
            project_id=project.id,
            parameters=json.dumps({
                "name": "strat",
                "parameters": {},
                "rules": {"entry": "close > 0", "exit": "close < 0"},
            }),
        )
        db.add(strategy)
        await db.flush()

        # AAPL: flat at 100 the whole time (no entry signal ever fires -> stays in cash)
        for i in range(5):
            db.add(MarketData(
                ticker="AAPL", date=datetime(2024, 1, i + 1), open="100", high="100",
                low="100", close="100", volume="1000",
            ))
        # MSFT: same, flat at 200
        for i in range(5):
            db.add(MarketData(
                ticker="MSFT", date=datetime(2024, 1, i + 1), open="200", high="200",
                low="200", close="200", volume="1000",
            ))
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_run_portfolio_backtest_allocates_and_aggregates(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        response = await portfolio_backtest_service.run_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
        )

    assert response["allocations"] == [
        {"ticker": "AAPL", "weight": pytest.approx(0.5)},
        {"ticker": "MSFT", "weight": pytest.approx(0.5)},
    ]
    # Neither ticker's "close > 0" entry rule ever fires against flat prices with no
    # prior bar transition here (rules mode only evaluates from index 1), so with a
    # constant entry condition true from bar 1 onward AAPL/MSFT do open a position;
    # the exact P&L isn't asserted — this test verifies orchestration and shape.
    assert set(response["per_ticker"].keys()) == {"AAPL", "MSFT"}
    assert response["per_ticker"]["AAPL"]["allocated_capital"] == pytest.approx(5000.0)
    assert response["per_ticker"]["MSFT"]["allocated_capital"] == pytest.approx(5000.0)
    assert len(response["equity_curve"]) == 5
    assert "return_pct" in response["metrics"]


@pytest.mark.asyncio
async def test_run_portfolio_backtest_rejects_insufficient_coverage(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        with pytest.raises(ValueError, match="AAPL"):
            await portfolio_backtest_service.run_portfolio_backtest(
                portfolio_seeded["strategy_id"],
                [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
                "2023-01-01", "2024-01-05",  # AAPL/MSFT data only starts 2024-01-01
                10000.0, 0.1, 0.05,
                db, user,
            )


@pytest.mark.asyncio
async def test_run_portfolio_backtest_does_not_raise_missing_greenlet_on_unauthorized(session_factory, portfolio_seeded):
    """Ownership-check regression, mirroring test_backtest_ownership.py: a user
    who doesn't own the project must get a clean error, not MissingGreenlet."""
    async with session_factory() as db:
        other_user = User(name="Bob", email="bob@example.com", password_hash="x")
        db.add(other_user)
        await db.commit()
        await db.refresh(other_user)

    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == other_user.id))
        bob = result.scalars().first()
        with pytest.raises(ValueError, match="Unauthorized"):
            await portfolio_backtest_service.run_portfolio_backtest(
                portfolio_seeded["strategy_id"],
                [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
                "2024-01-01", "2024-01-05",
                10000.0, 0.1, 0.05,
                db, bob,
            )
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k run_portfolio_backtest`
Expected: FAIL with `AttributeError: module 'services.portfolio_backtest_service' has no attribute 'run_portfolio_backtest'`

- [ ] **Step 3: Implement `run_portfolio_backtest`**

Add to the imports at the top of `BackEnd/services/portfolio_backtest_service.py`:

```python
import pandas as pd

from database.models import PortfolioBacktestResult, Strategy, User
from sqlalchemy.orm import selectinload
from services.strategy_executor import StrategyExecutor, max_drawdown_pct, sharpe_ratio
```

(`max_drawdown_pct`/`sharpe_ratio` are already imported from Task 6 — don't duplicate the line, merge into one `from services.strategy_executor import ...` import.)

Append the function:

```python
async def run_portfolio_backtest(
    strategy_id: int,
    tickers: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    db: AsyncSession,
    user: User,
) -> Dict[str, Any]:
    """Run a strategy independently across a basket of tickers, each funded
    from a fixed weight of initial_capital, and return the aggregated
    portfolio result. Raises ValueError for any validation/ownership/
    execution failure — callers (e.g. the router) translate that to an
    HTTP 400/403."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise ValueError("Strategy not found")
    if strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise ValueError("start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    allocations = normalize_weights(tickers)

    for alloc in allocations:
        await _check_ticker_coverage(alloc["ticker"], start_dt, end_dt, db)

    per_ticker_results: Dict[str, Any] = {}
    allocated_capital: Dict[str, float] = {}

    for alloc in allocations:
        ticker = alloc["ticker"]
        sub_capital = initial_capital * alloc["weight"]
        allocated_capital[ticker] = sub_capital

        data_result = await db.execute(
            select(MarketData).where(
                MarketData.ticker == ticker,
                MarketData.date >= start_dt,
                MarketData.date <= end_dt,
            ).order_by(MarketData.date)
        )
        rows = data_result.scalars().all()
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
            result = executor.backtest(
                df, initial_capital=sub_capital,
                commission_pct=commission_pct, slippage_pct=slippage_pct,
            )
        except Exception as e:
            raise ValueError(f"Backtest execution failed for {ticker}: {str(e)}")

        per_ticker_results[ticker] = {**result, "allocated_capital": sub_capital}

    portfolio_equity_curve = aggregate_equity_curves(
        {t: r["equity_curve"] for t, r in per_ticker_results.items()},
        allocated_capital,
    )
    metrics = aggregate_portfolio_metrics(per_ticker_results, portfolio_equity_curve, initial_capital)

    record = PortfolioBacktestResult(
        strategy_id=strategy_id,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allocations=allocations,
        results=metrics,
        equity_curve=portfolio_equity_curve,
        per_ticker=per_ticker_results,
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)

    return {
        "id": record.id,
        "strategy_id": strategy_id,
        "allocations": allocations,
        "metrics": metrics,
        "equity_curve": portfolio_equity_curve,
        "per_ticker": per_ticker_results,
        "created_at": record.created_at.isoformat(),
    }
```

Also add `from database.models import MarketData` if not already present from Task 4 (it should already be there — don't duplicate).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k run_portfolio_backtest`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full backend suite to confirm no regressions**

Run (from `BackEnd/`): `uv run pytest tests/ -v`
Expected: All tests PASS (existing single-ticker suite plus all new portfolio tests).

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add run_portfolio_backtest orchestrator"
```

---

### Task 8: `get_portfolio_backtest_results` / `get_portfolio_backtest_detail`

**Files:**
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `async def get_portfolio_backtest_results(strategy_id: int, db: AsyncSession, user: User) -> List[Dict]` (summary rows) and `async def get_portfolio_backtest_detail(portfolio_backtest_id: int, db: AsyncSession, user: User) -> Dict` (full detail incl. `per_ticker`). Both raise `ValueError("Unauthorized")` or `ValueError("... not found")`. Consumed by Task 9's router endpoints.

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
@pytest.mark.asyncio
async def test_get_portfolio_backtest_results_does_not_raise_missing_greenlet(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        await portfolio_backtest_service.run_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
        )

    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        results = await portfolio_backtest_service.get_portfolio_backtest_results(
            portfolio_seeded["strategy_id"], db, user,
        )
    assert len(results) == 1
    assert results[0]["strategy_id"] == portfolio_seeded["strategy_id"]
    assert results[0]["allocations"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_get_portfolio_backtest_detail_does_not_raise_missing_greenlet(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        created = await portfolio_backtest_service.run_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
        )

    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        detail = await portfolio_backtest_service.get_portfolio_backtest_detail(
            created["id"], db, user,
        )
    assert detail["id"] == created["id"]
    assert set(detail["per_ticker"].keys()) == {"AAPL", "MSFT"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k "get_portfolio_backtest"`
Expected: FAIL with `AttributeError: module 'services.portfolio_backtest_service' has no attribute 'get_portfolio_backtest_results'`

- [ ] **Step 3: Implement both functions**

Append to `BackEnd/services/portfolio_backtest_service.py`:

```python
async def get_portfolio_backtest_results(strategy_id: int, db: AsyncSession, user: User) -> List[Dict[str, Any]]:
    """Summary rows (no per_ticker payload) for all portfolio backtests of a strategy."""
    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if not strategy or strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    result = await db.execute(
        select(PortfolioBacktestResult)
        .where(PortfolioBacktestResult.strategy_id == strategy_id)
        .order_by(PortfolioBacktestResult.created_at.desc())
    )
    records = result.scalars().all()
    return [
        {
            "id": r.id,
            "strategy_id": r.strategy_id,
            "allocations": r.allocations,
            "metrics": r.results,
            "created_at": r.created_at.isoformat(),
        }
        for r in records
    ]


async def get_portfolio_backtest_detail(portfolio_backtest_id: int, db: AsyncSession, user: User) -> Dict[str, Any]:
    """Full detail for one portfolio backtest, including the per_ticker breakdown."""
    result = await db.execute(
        select(PortfolioBacktestResult)
        .options(selectinload(PortfolioBacktestResult.strategy).selectinload(Strategy.project))
        .where(PortfolioBacktestResult.id == portfolio_backtest_id)
    )
    record = result.scalars().first()
    if not record:
        raise ValueError("Portfolio backtest not found")
    if record.strategy.project.owner_id != user.id:
        raise ValueError("Unauthorized")

    return {
        "id": record.id,
        "strategy_id": record.strategy_id,
        "allocations": record.allocations,
        "metrics": record.results,
        "equity_curve": record.equity_curve,
        "per_ticker": record.per_ticker,
        "created_at": record.created_at.isoformat(),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k "get_portfolio_backtest"`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add services/portfolio_backtest_service.py tests/test_portfolio_backtest_service.py
git commit -m "Add get_portfolio_backtest_results/detail service functions"
```

---

### Task 9: Router endpoints

**Files:**
- Modify: `BackEnd/routers/backtest.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py` (append)

**Interfaces:**
- Produces: `POST /api/backtest/run-portfolio`, `GET /api/backtest/portfolio/results/{strategy_id}`, `GET /api/backtest/portfolio/{portfolio_backtest_id}` on the existing `backtest` router (`prefix="/api/backtest"`).
- Consumes: `run_portfolio_backtest`, `get_portfolio_backtest_results`, `get_portfolio_backtest_detail` (Tasks 7, 8).

- [ ] **Step 1: Write the failing test**

Append to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
from fastapi import HTTPException

from routers import backtest as backtest_router


@pytest.mark.asyncio
async def test_run_portfolio_backtest_endpoint_translates_value_error_to_400(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        req = backtest_router.PortfolioBacktestRequest(
            strategy_id=portfolio_seeded["strategy_id"],
            tickers=[
                backtest_router.PortfolioTickerWeight(ticker="AAPL", weight=1),
            ],  # only 1 ticker -> normalize_weights raises
            start_date="2024-01-01",
            end_date="2024-01-05",
        )
        with pytest.raises(HTTPException) as exc_info:
            await backtest_router.run_portfolio_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_run_portfolio_backtest_endpoint_succeeds(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        req = backtest_router.PortfolioBacktestRequest(
            strategy_id=portfolio_seeded["strategy_id"],
            tickers=[
                backtest_router.PortfolioTickerWeight(ticker="AAPL", weight=1),
                backtest_router.PortfolioTickerWeight(ticker="MSFT", weight=1),
            ],
            start_date="2024-01-01",
            end_date="2024-01-05",
        )
        response = await backtest_router.run_portfolio_backtest_endpoint(req, db=db, user=user)
    assert set(response["per_ticker"].keys()) == {"AAPL", "MSFT"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run (from `BackEnd/`): `uv run pytest tests/test_portfolio_backtest_service.py -v -k endpoint`
Expected: FAIL with `AttributeError: module 'routers.backtest' has no attribute 'PortfolioBacktestRequest'`

- [ ] **Step 3: Add the endpoints**

In `BackEnd/routers/backtest.py`, add to imports at the top:

```python
from typing import List
from services.backtest_service import run_backtest, get_backtest_results
from services.portfolio_backtest_service import (
    run_portfolio_backtest, get_portfolio_backtest_results, get_portfolio_backtest_detail,
)
```

(`run_backtest`/`get_backtest_results` are already imported by the existing line — merge, don't duplicate.)

Add after the existing `BacktestRequest` class:

```python
class PortfolioTickerWeight(BaseModel):
    ticker: str
    weight: float

class PortfolioBacktestRequest(BaseModel):
    strategy_id: int
    tickers: List[PortfolioTickerWeight]
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
```

Add after the existing endpoints, at the bottom of the file:

```python
@router.post("/run-portfolio")
async def run_portfolio_backtest_endpoint(
    req: PortfolioBacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Run a portfolio backtest: one strategy across a basket of tickers,
    each funded from a custom fixed weight of initial_capital."""
    try:
        return await run_portfolio_backtest(
            req.strategy_id,
            [t.model_dump() for t in req.tickers],
            req.start_date,
            req.end_date,
            req.initial_capital,
            req.commission_pct,
            req.slippage_pct,
            db,
            user,
        )
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))

@router.get("/portfolio/results/{strategy_id}")
async def get_portfolio_backtest_results_endpoint(
    strategy_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get all portfolio backtest results for a strategy"""
    try:
        return await get_portfolio_backtest_results(strategy_id, db, user)
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))

@router.get("/portfolio/{portfolio_backtest_id}")
async def get_portfolio_backtest_detail_endpoint(
    portfolio_backtest_id: int,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Get detailed portfolio backtest results, including per-ticker breakdown"""
    try:
        return await get_portfolio_backtest_detail(portfolio_backtest_id, db, user)
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else 404
        raise HTTPException(status_code=status_code, detail=str(e))
```

Also confirm `HTTPException` is imported at the top of the file (it currently is imported inline inside `get_backtest_detail` via `from fastapi import HTTPException` — add `HTTPException` to the top-level `from fastapi import APIRouter, Depends` line instead, i.e. `from fastapi import APIRouter, Depends, HTTPException`, and remove the two inline `from fastapi import HTTPException` lines inside `get_backtest_detail` since it's now imported at module level).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_portfolio_backtest_service.py -v -k endpoint`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full backend suite**

Run: `uv run pytest tests/ -v`
Expected: All PASS.

- [ ] **Step 6: Commit**

```bash
cd BackEnd
git add routers/backtest.py tests/test_portfolio_backtest_service.py
git commit -m "Add portfolio backtest router endpoints"
```

---

### Task 10: Frontend API client

**Files:**
- Modify: `FrontEnd/src/api/backtest.js`

**Interfaces:**
- Produces: `runPortfolioBacktest(strategyId, tickers, startDate, endDate, initialCapital, commissionPct, slippagePct)`, `getPortfolioBacktestResults(strategyId)`, `getPortfolioBacktestDetail(portfolioBacktestId)`. `tickers` is `[{ticker, weight}]`. Consumed by Tasks 11, 12.

- [ ] **Step 1: Add the functions**

Append to `FrontEnd/src/api/backtest.js`:

```javascript
export async function runPortfolioBacktest(strategyId, tickers, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05) {
  const { data } = await client.post('/api/backtest/run-portfolio', {
    strategy_id: strategyId,
    tickers,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
  });
  return data;
}

export async function getPortfolioBacktestResults(strategyId) {
  const { data } = await client.get(`/api/backtest/portfolio/results/${strategyId}`);
  return data;
}

export async function getPortfolioBacktestDetail(portfolioBacktestId) {
  const { data } = await client.get(`/api/backtest/portfolio/${portfolioBacktestId}`);
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
git commit -m "Add frontend API client for portfolio backtests"
```

---

### Task 11: `StrategiesPage.jsx` — Single/Portfolio toggle and form

**Files:**
- Modify: `FrontEnd/src/pages/StrategiesPage.jsx`

**Interfaces:**
- Consumes: `backtestApi.runPortfolioBacktest` (Task 10).

- [ ] **Step 1: Add portfolio mode state**

In `FrontEnd/src/pages/StrategiesPage.jsx`, after the existing `backtestForm` state declaration (around line 28-33), add:

```javascript
  const [backtestMode, setBacktestMode] = useState('single');
  const [portfolioRows, setPortfolioRows] = useState([
    { ticker: '', weight: 50 },
    { ticker: '', weight: 50 },
  ]);
```

- [ ] **Step 2: Add row-editing helpers**

After the `filtered` `useMemo` block (around line 99), add:

```javascript
  const updatePortfolioRow = (index, field, value) => {
    setPortfolioRows((prev) => prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)));
  };

  const addPortfolioRow = () => {
    setPortfolioRows((prev) => [...prev, { ticker: '', weight: 0 }]);
  };

  const removePortfolioRow = (index) => {
    setPortfolioRows((prev) => prev.filter((_, i) => i !== index));
  };

  const validPortfolioRows = portfolioRows.filter((r) => r.ticker && Number(r.weight) > 0);
```

- [ ] **Step 3: Branch `handleRunBacktest` on mode**

Replace the existing `handleRunBacktest` function (lines 122-145):

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
      if (backtestMode === 'portfolio') {
        await backtestApi.runPortfolioBacktest(
          selectedStrategyId,
          validPortfolioRows.map((r) => ({ ticker: r.ticker, weight: Number(r.weight) })),
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital
        );
      } else {
        await backtestApi.runBacktest(
          selectedStrategyId,
          backtestForm.ticker,
          backtestForm.startDate,
          backtestForm.endDate,
          backtestForm.initialCapital
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

- [ ] **Step 4: Add the mode toggle and portfolio row UI**

Replace the `<form className="stack" onSubmit={handleRunBacktest}>` block (lines 219-288) with:

```javascript
            <form className="stack" onSubmit={handleRunBacktest}>
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
              </div>

              <div className="layout two-cols">
                <label className="field">
                  <span>Select Strategy</span>
                  <select
                    value={selectedStrategyId || ''}
                    onChange={(e) => setSelectedStrategyId(Number(e.target.value) || null)}
                  >
                    <option value="">Choose a strategy...</option>
                    {filtered.map((s) => (
                      <option key={s.id} value={s.id}>{s.name}</option>
                    ))}
                  </select>
                </label>

                {backtestMode === 'single' && (
                  <label className="field">
                    <span>Ticker</span>
                    <select
                      value={backtestForm.ticker}
                      onChange={(e) => setBacktestForm({ ...backtestForm, ticker: e.target.value })}
                      disabled={tickersLoading || !tickers.length}
                    >
                      {!tickers.length && (
                        <option value="">
                          {tickersLoading ? 'Loading tickers...' : 'No market data uploaded yet'}
                        </option>
                      )}
                      {tickers.map((t) => (
                        <option key={t} value={t}>{t}</option>
                      ))}
                    </select>
                    {tickerRange && (
                      <span className="muted" style={{ fontSize: '0.8em' }}>
                        Data available {toDateInputValue(tickerRange.start_date)} to {toDateInputValue(tickerRange.end_date)}
                        {' '}({tickerRange.count} bars)
                      </span>
                    )}
                  </label>
                )}

                <label className="field">
                  <span>Start Date</span>
                  <input
                    type="date"
                    value={backtestForm.startDate}
                    min={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.start_date) : undefined}
                    max={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.end_date) : undefined}
                    onChange={(e) => setBacktestForm({ ...backtestForm, startDate: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>End Date</span>
                  <input
                    type="date"
                    value={backtestForm.endDate}
                    min={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.start_date) : undefined}
                    max={backtestMode === 'single' && tickerRange ? toDateInputValue(tickerRange.end_date) : undefined}
                    onChange={(e) => setBacktestForm({ ...backtestForm, endDate: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Initial Capital</span>
                  <input
                    type="number"
                    value={backtestForm.initialCapital}
                    onChange={(e) => setBacktestForm({ ...backtestForm, initialCapital: Number(e.target.value) })}
                  />
                </label>
              </div>

              {backtestMode === 'portfolio' && (
                <div className="stack" style={{ marginTop: '8px' }}>
                  <span className="muted" style={{ fontSize: '0.85em' }}>
                    Weights are relative — they don't need to sum to 100 (e.g. 2 and 1 splits 66/33).
                  </span>
                  {portfolioRows.map((row, index) => (
                    <div key={index} className="layout two-cols" style={{ alignItems: 'end' }}>
                      <label className="field">
                        <span>Ticker</span>
                        <select
                          value={row.ticker}
                          onChange={(e) => updatePortfolioRow(index, 'ticker', e.target.value)}
                          disabled={tickersLoading || !tickers.length}
                        >
                          <option value="">Choose a ticker...</option>
                          {tickers.map((t) => (
                            <option key={t} value={t}>{t}</option>
                          ))}
                        </select>
                      </label>
                      <label className="field">
                        <span>Weight</span>
                        <input
                          type="number"
                          min="0"
                          value={row.weight}
                          onChange={(e) => updatePortfolioRow(index, 'weight', e.target.value)}
                        />
                      </label>
                      <button
                        type="button"
                        className="ghost-btn"
                        onClick={() => removePortfolioRow(index)}
                        disabled={portfolioRows.length <= 2}
                      >
                        Remove
                      </button>
                    </div>
                  ))}
                  <button type="button" className="ghost-btn" onClick={addPortfolioRow}>
                    Add ticker
                  </button>
                </div>
              )}

              <button
                className="primary-btn"
                type="submit"
                disabled={
                  backtestLoading ||
                  !selectedStrategyId ||
                  (backtestMode === 'single' ? !backtestForm.ticker : validPortfolioRows.length < 2)
                }
              >
                {backtestLoading ? 'Running...' : 'Run Backtest'}
              </button>
            </form>
```

- [ ] **Step 5: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 6: Commit**

```bash
cd FrontEnd
git add src/pages/StrategiesPage.jsx
git commit -m "Add Single/Portfolio toggle to the backtest form"
```

---

### Task 12: `BacktestResultsPage.jsx` — merged results list and per-ticker breakdown

**Files:**
- Modify: `FrontEnd/src/pages/BacktestResultsPage.jsx`

**Interfaces:**
- Consumes: `backtestApi.getPortfolioBacktestResults`, `backtestApi.getPortfolioBacktestDetail` (Task 10), `BacktestChart` (existing, unmodified).

- [ ] **Step 1: Rewrite the page to merge single + portfolio results**

Replace the full contents of `FrontEnd/src/pages/BacktestResultsPage.jsx`:

```javascript
import React, { useEffect, useState } from 'react';
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

  const loadResults = async () => {
    setLoading(true);
    setError('');
    try {
      const [singleResults, portfolioResults] = await Promise.all([
        backtestApi.getBacktestResults(strategyId),
        backtestApi.getPortfolioBacktestResults(strategyId),
      ]);
      const merged = [
        ...(singleResults || []).map((r) => ({ ...r, _type: 'single' })),
        ...(portfolioResults || []).map((r) => ({ ...r, _type: 'portfolio' })),
      ].sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
      setResults(merged);
      if (merged.length > 0) {
        loadDetail(merged[0]);
      }
    } catch (err) {
      setError(err?.response?.data?.detail || 'Failed to load backtest results');
    } finally {
      setLoading(false);
    }
  };

  const loadDetail = async (result) => {
    setDetailLoading(true);
    setExpandedTicker(null);
    try {
      const data = result._type === 'portfolio'
        ? await backtestApi.getPortfolioBacktestDetail(result.id)
        : await backtestApi.getBacktestDetail(result.id);
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
                    {result._type === 'portfolio' ? (
                      <span className="chip">Portfolio · {result.allocations?.length || 0} tickers</span>
                    ) : (
                      <span className="chip">{result.num_trades} trades</span>
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

              {selectedResult._type === 'portfolio' ? (
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
              ) : (
                <>
                  <h3 style={{ marginTop: '20px' }}>Trades</h3>
                  <div className="trades-list">
                    {selectedResult.trades && selectedResult.trades.map((trade, idx) => (
                      <div key={idx} className="trade-item">
                        <span className={`badge ${trade.type === 'entry' ? 'entry' : 'exit'}`}>
                          {trade.type.toUpperCase()}
                        </span>
                        <span>${trade.price.toFixed(2)}</span>
                        <span className="muted">{trade.date}</span>
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

      {/* Aggregate chart (single-ticker: price+signals+equity; portfolio: equity only, per-ticker charts are inline above) */}
      {selectedResult && selectedResult._type === 'single' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals}
            trades={selectedResult.trades}
            equityCurve={selectedResult.equity_curve}
          />
        </div>
      )}
      {selectedResult && selectedResult._type === 'portfolio' && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Portfolio Equity</h3>
          <BacktestChart
            data={(selectedResult.equity_curve || []).map((p) => ({ date: p.date, close: p.equity, signal: 0 }))}
            trades={[]}
            equityCurve={selectedResult.equity_curve}
          />
        </div>
      )}
    </div>
  );
}
```

Note: the portfolio aggregate chart reuses `BacktestChart` by feeding the equity curve into its `data`/`close` slot (so the top price panel shows the aggregate equity line) since `BacktestChart` requires non-empty `data` to render at all — this avoids adding a new chart variant for a top-level aggregate-only view while still showing the equity panel (fed the real `equityCurve`) underneath.

- [ ] **Step 2: Verify the build**

Run (from `FrontEnd/`): `npm run build`
Expected: Build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
cd FrontEnd
git add src/pages/BacktestResultsPage.jsx
git commit -m "Show portfolio backtest results with per-ticker breakdown"
```

---

### Task 13: Manual end-to-end QA

**Files:** none (verification only)

- [ ] **Step 1: Start the stack**

From the repo root: `docker compose up --build` (or run backend/frontend dev servers separately per CLAUDE.md's Commands section). Confirm `BackEnd`'s tables include `portfolio_backtest_results` — since there's no migration tooling, if this is an existing dev DB, drop and let `init_db()` recreate it (or run against a fresh Postgres volume).

- [ ] **Step 2: Seed two tickers of market data**

Log in, go to `/data`, upload or import at least two tickers (e.g. via `POST /api/data/import/{ticker}` with a real Alpha Vantage key, or CSV upload) covering an overlapping date range.

- [ ] **Step 3: Create a strategy and run a portfolio backtest**

Go to a project's Strategies page, create a simple rules-based strategy (e.g. `fast_ma > slow_ma` / `fast_ma < slow_ma`), switch the backtest form to "Portfolio", add both tickers with weights, and submit.

- [ ] **Step 4: Verify the results page**

Confirm the results list shows a "Portfolio · 2 tickers" badge, the aggregate metrics render, the per-ticker breakdown table shows both tickers, and clicking a ticker row expands its own price/equity chart.

- [ ] **Step 5: Verify the error path**

Submit a portfolio backtest with a date range one of the tickers doesn't cover; confirm a clear error message naming the ticker is shown (not a generic 500).

- [ ] **Step 6: Confirm the single-ticker path still works**

Switch the toggle back to "Single ticker" and run a normal backtest, confirming the existing flow is unaffected.

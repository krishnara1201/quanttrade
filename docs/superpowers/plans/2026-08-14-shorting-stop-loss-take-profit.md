# Shorting + Stop-Loss/Take-Profit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add short-position support and stop-loss/take-profit forced exits to the backtest engine, exposed as optional per-run parameters on both single-ticker and portfolio backtests, with matching frontend controls and result display.

**Architecture:** `StrategyExecutor._execute_trades` (`BackEnd/services/strategy_executor.py`) is refactored to track explicit position state (`direction`/`qty`/`entry_price`/`entry_basis`) instead of an implicit long-only `shares` count, via two new helper methods `_open_position`/`_close_position`. A new `allow_short` flag reinterprets `signal == -1` as a short entry when flat; new `stop_loss_pct`/`take_profit_pct` params force a close-only exit each bar, checked before that bar's signal. All three are threaded through as new optional fields on `BacktestRequest`/`PortfolioBacktestRequest` → new nullable/boolean columns on `BacktestResult`/`PortfolioBacktestResult` → `StrategyExecutor.backtest()`.

**Tech Stack:** FastAPI, SQLAlchemy (async, `sqlite+aiosqlite` in tests), pandas, pytest/pytest-asyncio, React, Recharts.

**Spec:** `docs/superpowers/specs/2026-08-14-shorting-stop-loss-take-profit-design.md`

## Global Constraints

- `stop_loss_pct`/`take_profit_pct` are checked against bar **close only** — no intrabar high/low.
- SL/TP is checked **before** that bar's signal, so a forced exit can be followed by a same-bar re-entry.
- No margin/leverage modeling — short sizing reuses the existing all-in capital-based formula.
- `allow_short`/`stop_loss_pct`/`take_profit_pct` are **per backtest run**, not saved on the strategy.
- Trade record changes are additive only (`direction`, `exit_reason`) — never remove/rename existing fields (`type`, `price`, `date`, `size`, `pnl`).
- `stop_loss_pct`/`take_profit_pct`, when provided, must be `> 0` — reject with a 400/`ValueError` otherwise, matching each service function's existing error-raising convention (`HTTPException` in `backtest_service.py`, `ValueError` in `portfolio_backtest_service.py`).
- New service-layer keyword params (`allow_short`, `stop_loss_pct`, `take_profit_pct`) must be added **keyword-only** (after a bare `*`) at the end of `create_pending_backtest`/`create_pending_portfolio_backtest` signatures — both functions have several existing positional call sites across the test suite that must keep working unmodified.

---

## File Structure

- **Modify** `BackEnd/database/models.py` — add 3 columns each to `BacktestResult`, `PortfolioBacktestResult`.
- **Modify** `BackEnd/services/strategy_executor.py` — `_open_position`/`_close_position` helpers, rewritten `_execute_trades`, updated `backtest()` signature.
- **Modify** `BackEnd/services/backtest_service.py` — `create_pending_backtest`/`execute_backtest`.
- **Modify** `BackEnd/services/portfolio_backtest_service.py` — `create_pending_portfolio_backtest`/`execute_portfolio_backtest`.
- **Modify** `BackEnd/routers/backtest.py` — `BacktestRequest`/`PortfolioBacktestRequest`, both endpoint call sites.
- **Modify** `BackEnd/tests/test_async_task_schema.py`, `test_strategy_executor.py`, `test_backtest_ownership.py`, `test_portfolio_backtest_service.py`.
- **Modify** `FrontEnd/src/api/backtest.js`, `src/pages/StrategiesPage.jsx`, `src/pages/BacktestResultsPage.jsx`, `src/components/BacktestChart.jsx`, `src/styles.css`.

---

### Task 1: Database schema — shorting/SL/TP columns

**Files:**
- Modify: `BackEnd/database/models.py:63-100` (`BacktestResult`, `PortfolioBacktestResult`)
- Test: `BackEnd/tests/test_async_task_schema.py`

**Interfaces:**
- Produces: `BacktestResult.allow_short` (`bool`, default `False`), `.stop_loss_pct`/`.take_profit_pct` (`float | None`); same 3 columns on `PortfolioBacktestResult`. Later tasks read/write these directly on ORM instances.

- [ ] **Step 1: Write the failing test**

Add to `BackEnd/tests/test_async_task_schema.py`:

```python
@pytest.mark.asyncio
async def test_backtest_result_has_shorting_and_stop_loss_take_profit_columns(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="s", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        record = BacktestResult(
            strategy_id=strategy.id,
            ticker="AAPL",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    assert record.allow_short is False
    assert record.stop_loss_pct is None
    assert record.take_profit_pct is None


@pytest.mark.asyncio
async def test_portfolio_backtest_result_has_shorting_and_stop_loss_take_profit_columns(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="p", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="s", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        record = PortfolioBacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            allocations=[{"ticker": "AAPL", "weight": 1.0}],
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

    assert record.allow_short is False
    assert record.stop_loss_pct is None
    assert record.take_profit_pct is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_async_task_schema.py -v`
Expected: FAIL with `AttributeError: 'BacktestResult' object has no attribute 'allow_short'` (or similar) on both new tests.

- [ ] **Step 3: Add the columns**

In `BackEnd/database/models.py`, in `class BacktestResult`, right after `slippage_pct = Column(Float, nullable=True)`:

```python
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
```

In `class PortfolioBacktestResult`, right after `slippage_pct = Column(Float, nullable=False)`:

```python
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
```

`Boolean`/`Float` are already imported at the top of this file — no import changes needed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_async_task_schema.py -v`
Expected: PASS (all tests in the file, including the two new ones).

- [ ] **Step 5: Commit**

```bash
git add BackEnd/database/models.py BackEnd/tests/test_async_task_schema.py
git commit -m "Add allow_short/stop_loss_pct/take_profit_pct columns to backtest tables"
```

---

### Task 2: `_execute_trades` position-state refactor (regression-safe)

**Files:**
- Modify: `BackEnd/services/strategy_executor.py:216-267` (`_execute_trades`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Produces: `StrategyExecutor._open_position(direction, close_price, cash, commission_pct, slippage_pct, timestamp, trades) -> (direction, qty, entry_price, entry_basis, cash)`; `StrategyExecutor._close_position(direction, qty, entry_basis, close_price, cash, commission_pct, slippage_pct, timestamp, exit_reason, trades) -> (direction, qty, entry_price, entry_basis, cash)` (direction/qty/entry_price/entry_basis are always `(None, 0, 0.0, 0.0)` on close). Trade dicts now include a `'direction'` key on entries (this task always passes `'long'`); exits gain an `'exit_reason'` key (this task always passes `'signal'`).
- Consumes: nothing new — same `df`/`initial_capital`/`commission_pct`/`slippage_pct` as before.

This task must not change `_execute_trades`'s observable behavior for the existing long-only, no-SL/TP path — it only restructures the internals so Tasks 3 and 4 can extend them additively.

- [ ] **Step 1: Write the failing test**

Add to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_open_and_close_position_helpers_match_original_long_math():
    executor = make_executor()
    trades = []

    direction, qty, entry_price, entry_basis, cash = executor._open_position(
        'long', close_price=100.0, cash=1000.0, commission_pct=1.0, slippage_pct=0.5,
        timestamp='2024-01-01T00:00:00', trades=trades,
    )
    assert direction == 'long'
    assert qty == 9
    assert entry_price == pytest.approx(100.5, abs=1e-6)
    assert entry_basis == pytest.approx(913.545, abs=1e-3)
    assert cash == pytest.approx(86.455, abs=1e-3)
    assert trades == [{
        'type': 'entry', 'direction': 'long',
        'price': pytest.approx(100.5, abs=1e-6), 'date': '2024-01-01T00:00:00', 'size': 9,
    }]

    direction, qty, entry_price, entry_basis, cash = executor._close_position(
        direction, qty, entry_basis, close_price=110.0, cash=cash,
        commission_pct=1.0, slippage_pct=0.5, timestamp='2024-01-02T00:00:00',
        exit_reason='signal', trades=trades,
    )
    assert direction is None
    assert qty == 0
    assert cash == pytest.approx(1061.6545, abs=1e-3)
    assert trades[1]['type'] == 'exit'
    assert trades[1]['direction'] == 'long'
    assert trades[1]['exit_reason'] == 'signal'
    assert trades[1]['pnl'] == pytest.approx(61.6545, abs=1e-3)


def test_open_position_returns_flat_when_cash_cannot_afford_one_share():
    executor = make_executor()
    trades = []
    direction, qty, entry_price, entry_basis, cash = executor._open_position(
        'long', close_price=1000.0, cash=500.0, commission_pct=1.0, slippage_pct=0.5,
        timestamp='2024-01-01T00:00:00', trades=trades,
    )
    assert direction is None
    assert qty == 0
    assert cash == pytest.approx(500.0)
    assert trades == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -k position_helpers -v`
Expected: FAIL with `AttributeError: 'StrategyExecutor' object has no attribute '_open_position'`.

- [ ] **Step 3: Add the helpers and rewrite `_execute_trades`**

In `BackEnd/services/strategy_executor.py`, replace the entire `_execute_trades` method (lines 216-267) with:

```python
    def _open_position(self, direction: str, close_price: float, cash: float,
                        commission_pct: float, slippage_pct: float, timestamp: str,
                        trades: List[Dict]):
        """Open a long or short position, sized by full available cash (all-in,
        max whole shares affordable at the commission-inclusive fill price — the
        same formula for both directions, no margin modeling). Returns
        (direction, qty, entry_price, entry_basis, cash); direction/qty stay
        (None, 0) if cash can't afford even one share."""
        if direction == 'long':
            fill_price = close_price * (1 + slippage_pct / 100)
        else:
            fill_price = close_price * (1 - slippage_pct / 100)
        effective_price = fill_price * (1 + commission_pct / 100)
        qty = int(cash // effective_price)
        if qty <= 0:
            return None, 0, 0.0, 0.0, cash

        commission = qty * fill_price * (commission_pct / 100)
        if direction == 'long':
            entry_basis = qty * fill_price + commission
            cash -= entry_basis
        else:
            entry_basis = qty * fill_price - commission
            cash += entry_basis

        trades.append({
            'type': 'entry',
            'direction': direction,
            'price': float(fill_price),
            'date': timestamp,
            'size': qty,
        })
        return direction, qty, fill_price, entry_basis, cash

    def _close_position(self, direction: str, qty: int, entry_basis: float,
                         close_price: float, cash: float, commission_pct: float,
                         slippage_pct: float, timestamp: str, exit_reason: str,
                         trades: List[Dict]):
        """Close an open long or short position at the bar's close (adjusted for
        slippage against the trader), recording pnl and exit_reason on the trade.
        Returns (None, 0, 0.0, 0.0, cash) — direction/qty/entry_price/entry_basis
        all reset since the position is now flat."""
        if direction == 'long':
            fill_price = close_price * (1 - slippage_pct / 100)
            proceeds = qty * fill_price
            commission = proceeds * (commission_pct / 100)
            net_proceeds = proceeds - commission
            pnl = net_proceeds - entry_basis
            cash += net_proceeds
        else:
            fill_price = close_price * (1 + slippage_pct / 100)
            cost = qty * fill_price
            commission = cost * (commission_pct / 100)
            total_cost = cost + commission
            pnl = entry_basis - total_cost
            cash -= total_cost

        trades.append({
            'type': 'exit',
            'direction': direction,
            'price': float(fill_price),
            'date': timestamp,
            'size': qty,
            'pnl': float(pnl),
            'exit_reason': exit_reason,
        })
        return None, 0, 0.0, 0.0, cash

    def _execute_trades(self, df: pd.DataFrame, initial_capital: float,
                         commission_pct: float = 0.1, slippage_pct: float = 0.05):
        """Execute trades based on signals using capital-based sizing with commission/slippage"""
        trades = []
        equity_curve = []
        cash = initial_capital
        direction = None
        qty = 0
        entry_price = 0.0
        entry_basis = 0.0

        for i in range(len(df)):
            signal = df['signal'].iloc[i] if 'signal' in df.columns else 0
            close_price = df['close'].iloc[i]
            timestamp = self._format_date(df.index[i])

            if signal == 1 and direction is None:
                direction, qty, entry_price, entry_basis, cash = self._open_position(
                    'long', close_price, cash, commission_pct, slippage_pct, timestamp, trades,
                )
            elif signal == -1 and direction == 'long':
                direction, qty, entry_price, entry_basis, cash = self._close_position(
                    direction, qty, entry_basis, close_price, cash,
                    commission_pct, slippage_pct, timestamp, 'signal', trades,
                )

            equity = cash + (qty * close_price if direction == 'long' else 0)
            equity_curve.append({'date': timestamp, 'equity': float(equity)})

        return trades, equity_curve
```

- [ ] **Step 4: Run tests to verify they pass, and confirm zero regressions**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -v`
Expected: PASS — the 2 new tests plus every pre-existing test in the file (in particular `test_execute_trades_applies_capital_sizing_commission_and_slippage` and `test_execute_trades_skips_entry_when_cash_cannot_afford_one_share`, which assert the exact numeric values this refactor must reproduce).

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "Refactor _execute_trades to explicit position-state helpers (no behavior change)"
```

---

### Task 3: Short positions (`allow_short`)

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_execute_trades`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Produces: `_execute_trades(..., allow_short: bool = False)`. With `allow_short=True`, `signal=-1` while flat opens a short (`direction='short'` trade), and `signal=1` while short closes it and can then open a new long same bar.
- Consumes: `_open_position`/`_close_position` from Task 2, unchanged.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_execute_trades_allow_short_false_ignores_negative_signal_when_flat():
    closes = [100, 100, 90]
    df = make_price_df(closes)
    df["signal"] = [0, -1, 0]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=1.0, slippage_pct=0.5,
    )

    assert trades == []
    assert all(pt['equity'] == pytest.approx(1000.0) for pt in equity_curve)


def test_execute_trades_allow_short_opens_short_and_can_reflip_to_long_same_bar():
    closes = [100, 100, 90]
    df = make_price_df(closes)
    df["signal"] = [0, -1, 1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=1.0, slippage_pct=0.5, allow_short=True,
    )

    assert len(trades) == 3
    short_entry, short_exit, long_entry = trades

    assert short_entry == {
        'type': 'entry', 'direction': 'short',
        'price': pytest.approx(99.5, abs=1e-6), 'date': df.index[1].isoformat(), 'size': 9,
    }
    assert short_exit['type'] == 'exit'
    assert short_exit['direction'] == 'short'
    assert short_exit['exit_reason'] == 'signal'
    assert short_exit['price'] == pytest.approx(90.45, abs=1e-6)
    assert short_exit['pnl'] == pytest.approx(64.3545, abs=1e-3)
    assert long_entry == {
        'type': 'entry', 'direction': 'long',
        'price': pytest.approx(90.45, abs=1e-6), 'date': df.index[2].isoformat(), 'size': 11,
    }

    assert equity_curve[0]['equity'] == pytest.approx(1000.0, abs=1e-6)
    assert equity_curve[1]['equity'] == pytest.approx(986.545, abs=1e-3)
    assert equity_curve[2]['equity'] == pytest.approx(1049.455, abs=1e-3)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -k allow_short -v`
Expected: FAIL — `_execute_trades() got an unexpected keyword argument 'allow_short'`.

- [ ] **Step 3: Add `allow_short` handling**

In `BackEnd/services/strategy_executor.py`, replace the `_execute_trades` signature and its signal-handling block:

```python
    def _execute_trades(self, df: pd.DataFrame, initial_capital: float,
                         commission_pct: float = 0.1, slippage_pct: float = 0.05,
                         allow_short: bool = False):
        """Execute trades based on signals using capital-based sizing with
        commission/slippage. allow_short=True reinterprets signal=-1 as a short
        entry when flat (still an exit when long); signal=1 while short covers
        it and can then open a new long the same bar."""
        trades = []
        equity_curve = []
        cash = initial_capital
        direction = None
        qty = 0
        entry_price = 0.0
        entry_basis = 0.0

        for i in range(len(df)):
            signal = df['signal'].iloc[i] if 'signal' in df.columns else 0
            close_price = df['close'].iloc[i]
            timestamp = self._format_date(df.index[i])

            if signal == 1:
                if direction == 'short':
                    direction, qty, entry_price, entry_basis, cash = self._close_position(
                        direction, qty, entry_basis, close_price, cash,
                        commission_pct, slippage_pct, timestamp, 'signal', trades,
                    )
                if direction is None:
                    direction, qty, entry_price, entry_basis, cash = self._open_position(
                        'long', close_price, cash, commission_pct, slippage_pct, timestamp, trades,
                    )
            elif signal == -1:
                if direction == 'long':
                    direction, qty, entry_price, entry_basis, cash = self._close_position(
                        direction, qty, entry_basis, close_price, cash,
                        commission_pct, slippage_pct, timestamp, 'signal', trades,
                    )
                if direction is None and allow_short:
                    direction, qty, entry_price, entry_basis, cash = self._open_position(
                        'short', close_price, cash, commission_pct, slippage_pct, timestamp, trades,
                    )

            if direction == 'long':
                equity = cash + qty * close_price
            elif direction == 'short':
                equity = cash - qty * close_price
            else:
                equity = cash
            equity_curve.append({'date': timestamp, 'equity': float(equity)})

        return trades, equity_curve
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -v`
Expected: PASS — all tests, including Task 2's and the original pre-existing tests (default `allow_short=False` keeps old behavior).

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "Add allow_short flag for short positions to _execute_trades"
```

---

### Task 4: Stop-loss / take-profit + `backtest()` wiring

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_execute_trades`, `backtest`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Produces: `_execute_trades(..., stop_loss_pct: float = None, take_profit_pct: float = None)`; `StrategyExecutor.backtest(df, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05, allow_short=False, stop_loss_pct=None, take_profit_pct=None)`.
- Consumes: Task 3's `allow_short`-aware `_execute_trades`.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_execute_trades_take_profit_closes_short_position():
    closes = [100, 100, 90]
    df = make_price_df(closes)
    df["signal"] = [0, -1, 0]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=1.0, slippage_pct=0.5,
        allow_short=True, take_profit_pct=5.0,
    )

    assert len(trades) == 2
    entry, exit_ = trades
    assert entry['direction'] == 'short'
    assert exit_['direction'] == 'short'
    assert exit_['exit_reason'] == 'take_profit'
    assert exit_['price'] == pytest.approx(90.45, abs=1e-6)
    assert exit_['pnl'] == pytest.approx(64.3545, abs=1e-3)
    assert equity_curve[-1]['equity'] == pytest.approx(1064.3545, abs=1e-3)


def test_execute_trades_stop_loss_forces_exit_then_signal_can_reenter_same_bar():
    closes = [100, 100, 96]
    df = make_price_df(closes)
    df["signal"] = [0, 1, 1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=0.0, slippage_pct=0.0,
        stop_loss_pct=3.0,
    )

    assert len(trades) == 3
    first_entry, stop_exit, reentry = trades
    assert first_entry == {
        'type': 'entry', 'direction': 'long',
        'price': 100.0, 'date': df.index[1].isoformat(), 'size': 10,
    }
    assert stop_exit['type'] == 'exit'
    assert stop_exit['exit_reason'] == 'stop_loss'
    assert stop_exit['price'] == pytest.approx(96.0, abs=1e-6)
    assert stop_exit['pnl'] == pytest.approx(-40.0, abs=1e-6)
    assert reentry == {
        'type': 'entry', 'direction': 'long',
        'price': 96.0, 'date': df.index[2].isoformat(), 'size': 10,
    }
    assert equity_curve[-1]['equity'] == pytest.approx(960.0, abs=1e-6)


def test_backtest_passes_allow_short_and_stop_loss_take_profit_through_to_execute_trades():
    closes = [100, 101, 99, 102, 105, 103, 108]
    df = make_price_df(closes)
    config = {
        "name": "always-short",
        "parameters": {},
        "rules": {"entry": "close > 100000", "exit": "close < 100000"},
    }
    executor = StrategyExecutor(config)

    result = executor.backtest(
        df, initial_capital=1000.0, commission_pct=0.1, slippage_pct=0.05,
        allow_short=True, stop_loss_pct=50.0, take_profit_pct=50.0,
    )

    assert any(
        t['type'] == 'entry' and t['direction'] == 'short' for t in result['trades']
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -k "take_profit_closes or stop_loss_forces or passes_allow_short" -v`
Expected: FAIL — `_execute_trades()`/`backtest()` reject the unexpected `stop_loss_pct`/`take_profit_pct` keyword arguments.

- [ ] **Step 3: Add stop-loss/take-profit checking and wire `backtest()`**

In `BackEnd/services/strategy_executor.py`, update `_execute_trades`'s signature and insert the forced-exit check at the top of the loop body (right after `timestamp = ...`, before the `if signal == 1:` block):

```python
    def _execute_trades(self, df: pd.DataFrame, initial_capital: float,
                         commission_pct: float = 0.1, slippage_pct: float = 0.05,
                         allow_short: bool = False,
                         stop_loss_pct: float = None, take_profit_pct: float = None):
        """Execute trades based on signals using capital-based sizing with
        commission/slippage. allow_short=True reinterprets signal=-1 as a short
        entry when flat (still an exit when long); signal=1 while short covers
        it and can then open a new long the same bar. stop_loss_pct/take_profit_pct,
        if set, force a close-only exit each bar (checked against that bar's
        close, before the bar's own signal is evaluated) independent of signal —
        so a forced exit can still be followed by a same-bar re-entry."""
        trades = []
        equity_curve = []
        cash = initial_capital
        direction = None
        qty = 0
        entry_price = 0.0
        entry_basis = 0.0

        for i in range(len(df)):
            signal = df['signal'].iloc[i] if 'signal' in df.columns else 0
            close_price = df['close'].iloc[i]
            timestamp = self._format_date(df.index[i])

            if direction is not None:
                exit_reason = None
                if direction == 'long':
                    if stop_loss_pct and close_price <= entry_price * (1 - stop_loss_pct / 100):
                        exit_reason = 'stop_loss'
                    elif take_profit_pct and close_price >= entry_price * (1 + take_profit_pct / 100):
                        exit_reason = 'take_profit'
                else:
                    if stop_loss_pct and close_price >= entry_price * (1 + stop_loss_pct / 100):
                        exit_reason = 'stop_loss'
                    elif take_profit_pct and close_price <= entry_price * (1 - take_profit_pct / 100):
                        exit_reason = 'take_profit'
                if exit_reason:
                    direction, qty, entry_price, entry_basis, cash = self._close_position(
                        direction, qty, entry_basis, close_price, cash,
                        commission_pct, slippage_pct, timestamp, exit_reason, trades,
                    )

            if signal == 1:
                if direction == 'short':
                    direction, qty, entry_price, entry_basis, cash = self._close_position(
                        direction, qty, entry_basis, close_price, cash,
                        commission_pct, slippage_pct, timestamp, 'signal', trades,
                    )
                if direction is None:
                    direction, qty, entry_price, entry_basis, cash = self._open_position(
                        'long', close_price, cash, commission_pct, slippage_pct, timestamp, trades,
                    )
            elif signal == -1:
                if direction == 'long':
                    direction, qty, entry_price, entry_basis, cash = self._close_position(
                        direction, qty, entry_basis, close_price, cash,
                        commission_pct, slippage_pct, timestamp, 'signal', trades,
                    )
                if direction is None and allow_short:
                    direction, qty, entry_price, entry_basis, cash = self._open_position(
                        'short', close_price, cash, commission_pct, slippage_pct, timestamp, trades,
                    )

            if direction == 'long':
                equity = cash + qty * close_price
            elif direction == 'short':
                equity = cash - qty * close_price
            else:
                equity = cash
            equity_curve.append({'date': timestamp, 'equity': float(equity)})

        return trades, equity_curve
```

Then update `backtest()`'s signature (around line 58) and its call to `_execute_trades` (around line 97):

```python
    def backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0,
                 commission_pct: float = 0.1, slippage_pct: float = 0.05,
                 allow_short: bool = False, stop_loss_pct: float = None,
                 take_profit_pct: float = None) -> Dict[str, Any]:
```

```python
        trades, equity_curve = self._execute_trades(
            df, initial_capital, commission_pct, slippage_pct,
            allow_short=allow_short, stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct,
        )
```

(Leave the rest of `backtest()` — the `mode == 'custom_code'` branch, `_calculate_metrics` call, and return dict — unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_strategy_executor.py -v`
Expected: PASS — every test in the file.

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "Add stop-loss/take-profit forced exits and wire allow_short/SL/TP through backtest()"
```

---

### Task 5: Single-ticker backend wiring

**Files:**
- Modify: `BackEnd/routers/backtest.py:19-26` (`BacktestRequest`), `:41-65` (`run_backtest_endpoint`)
- Modify: `BackEnd/services/backtest_service.py:1-121` (`create_pending_backtest`, `execute_backtest`)
- Test: `BackEnd/tests/test_backtest_ownership.py`

**Interfaces:**
- Produces: `BacktestRequest.allow_short: bool = False`, `.stop_loss_pct: Optional[float] = None`, `.take_profit_pct: Optional[float] = None`. `create_pending_backtest(strategy_id, ticker, start_date, end_date, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05, db=Depends(get_db), user=Depends(get_current_user), *, allow_short: bool = False, stop_loss_pct: Optional[float] = None, take_profit_pct: Optional[float] = None) -> BacktestResult` — raises `HTTPException(400, ...)` if `stop_loss_pct`/`take_profit_pct` is provided and `<= 0`.
- Consumes: Task 4's `StrategyExecutor.backtest(..., allow_short=..., stop_loss_pct=..., take_profit_pct=...)`; Task 1's new `BacktestResult` columns.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_backtest_ownership.py`:

```python
@pytest.mark.asyncio
async def test_create_pending_backtest_persists_allow_short_and_stop_loss_take_profit(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
            allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
    assert record.allow_short is True
    assert record.stop_loss_pct == 2.5
    assert record.take_profit_pct == 5.0


@pytest.mark.asyncio
async def test_create_pending_backtest_rejects_non_positive_stop_loss_pct(session_factory, seeded):
    from fastapi import HTTPException

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        with pytest.raises(HTTPException) as exc_info:
            await backtest_service.create_pending_backtest(
                seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
                db=db, user=user, stop_loss_pct=0,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_execute_backtest_passes_allow_short_and_stop_loss_take_profit_to_executor(session_factory, seeded, monkeypatch):
    from services.strategy_executor import StrategyExecutor
    captured = {}
    original_backtest = StrategyExecutor.backtest

    def spying_backtest(self, df, **kwargs):
        captured.update(kwargs)
        return original_backtest(self, df, **kwargs)

    monkeypatch.setattr(StrategyExecutor, "backtest", spying_backtest)

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user, allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
        await backtest_service.execute_backtest(record.id, db)

    assert captured["allow_short"] is True
    assert captured["stop_loss_pct"] == 2.5
    assert captured["take_profit_pct"] == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_backtest_ownership.py -k "allow_short_and_stop_loss or rejects_non_positive or passes_allow_short_and_stop_loss" -v`
Expected: FAIL — `create_pending_backtest() got an unexpected keyword argument 'allow_short'`.

- [ ] **Step 3: Wire the parameters through**

In `BackEnd/services/backtest_service.py`, add the import and update `create_pending_backtest`'s signature and body:

```python
from typing import Optional
```//add near the top, alongside the existing imports

```python
async def create_pending_backtest(strategy_id: int, ticker: str, start_date: str, end_date: str,
                       initial_capital: float = 10000.0,
                       commission_pct: float = 0.1,
                       slippage_pct: float = 0.05,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user),
                       *,
                       allow_short: bool = False,
                       stop_loss_pct: Optional[float] = None,
                       take_profit_pct: Optional[float] = None) -> BacktestResult:
    """Validate ownership/input and insert a pending BacktestResult row.
    The actual computation happens later in execute_backtest, run
    out-of-process by a Celery worker (see tasks.py)."""

    strategy_result = await db.execute(
        select(Strategy).options(selectinload(Strategy.project)).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    try:
        start_dt = datetime.fromisoformat(start_date)
        end_dt = datetime.fromisoformat(end_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_date and end_date must be ISO-formatted dates (YYYY-MM-DD)")

    if stop_loss_pct is not None and stop_loss_pct <= 0:
        raise HTTPException(status_code=400, detail="stop_loss_pct must be positive")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise HTTPException(status_code=400, detail="take_profit_pct must be positive")

    result_record = BacktestResult(
        strategy_id=strategy_id,
        ticker=ticker,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allow_short=allow_short,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        status="pending",
    )
    db.add(result_record)
    await db.commit()
    await db.refresh(result_record)
    return result_record
```

In `execute_backtest`, update the `executor.backtest(...)` call:

```python
        executor = StrategyExecutor(strategy.parameters, code=strategy.code)
        backtest_results = executor.backtest(
            df, initial_capital=record.initial_capital,
            commission_pct=record.commission_pct, slippage_pct=record.slippage_pct,
            allow_short=record.allow_short, stop_loss_pct=record.stop_loss_pct,
            take_profit_pct=record.take_profit_pct,
        )
```

In `BackEnd/routers/backtest.py`, add the 3 fields to `BacktestRequest`:

```python
class BacktestRequest(BaseModel):
    strategy_id: int
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
```

And update `run_backtest_endpoint`'s call:

```python
    record = await create_pending_backtest(
        req.strategy_id, req.ticker, req.start_date, req.end_date,
        req.initial_capital, req.commission_pct, req.slippage_pct,
        db, user,
        allow_short=req.allow_short, stop_loss_pct=req.stop_loss_pct, take_profit_pct=req.take_profit_pct,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_backtest_ownership.py -v`
Expected: PASS — all tests in the file, including the 3 new ones and every pre-existing test (whose positional `create_pending_backtest(...)` calls are unaffected since the new params are keyword-only).

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/backtest_service.py BackEnd/routers/backtest.py BackEnd/tests/test_backtest_ownership.py
git commit -m "Wire allow_short/stop_loss_pct/take_profit_pct through single-ticker backtest API"
```

---

### Task 6: Portfolio backend wiring

**Files:**
- Modify: `BackEnd/routers/backtest.py:32-39` (`PortfolioBacktestRequest`), `:109-145` (`run_portfolio_backtest_endpoint`)
- Modify: `BackEnd/services/portfolio_backtest_service.py`
- Test: `BackEnd/tests/test_portfolio_backtest_service.py`

**Interfaces:**
- Produces: `PortfolioBacktestRequest.allow_short: bool = False`, `.stop_loss_pct`/`.take_profit_pct: Optional[float] = None`. `create_pending_portfolio_backtest(strategy_id, tickers, start_date, end_date, initial_capital, commission_pct, slippage_pct, db, user, *, allow_short: bool = False, stop_loss_pct: Optional[float] = None, take_profit_pct: Optional[float] = None) -> PortfolioBacktestResult` — raises `ValueError` if `stop_loss_pct`/`take_profit_pct` is provided and `<= 0` (router already translates non-"Unauthorized"/"not found" `ValueError`s to a 400).
- Consumes: Task 4's `StrategyExecutor.backtest(..., allow_short=..., stop_loss_pct=..., take_profit_pct=...)`; Task 1's new `PortfolioBacktestResult` columns.

- [ ] **Step 1: Write the failing tests**

Add to `BackEnd/tests/test_portfolio_backtest_service.py`:

```python
@pytest.mark.asyncio
async def test_create_pending_portfolio_backtest_persists_allow_short_and_stop_loss_take_profit(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        record = await portfolio_backtest_service.create_pending_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
            allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
    assert record.allow_short is True
    assert record.stop_loss_pct == 2.5
    assert record.take_profit_pct == 5.0


@pytest.mark.asyncio
async def test_create_pending_portfolio_backtest_rejects_non_positive_take_profit_pct(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        with pytest.raises(ValueError, match="take_profit_pct must be positive"):
            await portfolio_backtest_service.create_pending_portfolio_backtest(
                portfolio_seeded["strategy_id"],
                [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
                "2024-01-01", "2024-01-05",
                10000.0, 0.1, 0.05,
                db, user,
                take_profit_pct=-1.0,
            )


@pytest.mark.asyncio
async def test_execute_portfolio_backtest_passes_allow_short_and_stop_loss_take_profit_to_executor(session_factory, portfolio_seeded, monkeypatch):
    from services.strategy_executor import StrategyExecutor
    captured_calls = []
    original_backtest = StrategyExecutor.backtest

    def spying_backtest(self, df, **kwargs):
        captured_calls.append(kwargs)
        return original_backtest(self, df, **kwargs)

    monkeypatch.setattr(StrategyExecutor, "backtest", spying_backtest)

    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        record = await portfolio_backtest_service.create_pending_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
            allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
        await portfolio_backtest_service.execute_portfolio_backtest(record.id, db)

    assert len(captured_calls) == 2  # one per ticker (AAPL, MSFT)
    for kwargs in captured_calls:
        assert kwargs["allow_short"] is True
        assert kwargs["stop_loss_pct"] == 2.5
        assert kwargs["take_profit_pct"] == 5.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd BackEnd && uv run pytest tests/test_portfolio_backtest_service.py -k "allow_short_and_stop_loss or rejects_non_positive_take_profit or passes_allow_short_and_stop_loss_take_profit_to_executor" -v`
Expected: FAIL — `create_pending_portfolio_backtest() got an unexpected keyword argument 'allow_short'`.

- [ ] **Step 3: Wire the parameters through**

In `BackEnd/services/portfolio_backtest_service.py`, update the `typing` import:

```python
from typing import Any, Dict, List, Optional
```

Update `create_pending_portfolio_backtest`'s signature and body:

```python
async def create_pending_portfolio_backtest(
    strategy_id: int,
    tickers: List[Dict[str, Any]],
    start_date: str,
    end_date: str,
    initial_capital: float,
    commission_pct: float,
    slippage_pct: float,
    db: AsyncSession,
    user: User,
    *,
    allow_short: bool = False,
    stop_loss_pct: Optional[float] = None,
    take_profit_pct: Optional[float] = None,
) -> PortfolioBacktestResult:
    """Validate ownership/input/ticker-coverage and insert a pending
    PortfolioBacktestResult row. The actual per-ticker execution happens
    later in execute_portfolio_backtest, run out-of-process by a Celery
    worker. Raises ValueError for any validation/ownership failure —
    callers (e.g. the router) translate that to an HTTP 400/403/404."""
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

    if stop_loss_pct is not None and stop_loss_pct <= 0:
        raise ValueError("stop_loss_pct must be positive")
    if take_profit_pct is not None and take_profit_pct <= 0:
        raise ValueError("take_profit_pct must be positive")

    allocations = normalize_weights(tickers)

    for alloc in allocations:
        await _check_ticker_coverage(alloc["ticker"], start_dt, end_dt, db)

    record = PortfolioBacktestResult(
        strategy_id=strategy_id,
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=initial_capital,
        commission_pct=commission_pct,
        slippage_pct=slippage_pct,
        allow_short=allow_short,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        allocations=allocations,
        status="pending",
    )
    db.add(record)
    await db.commit()
    await db.refresh(record)
    return record
```

In `execute_portfolio_backtest`, update the per-ticker `executor.backtest(...)` call:

```python
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
```

In `BackEnd/routers/backtest.py`, add the 3 fields to `PortfolioBacktestRequest`:

```python
class PortfolioBacktestRequest(BaseModel):
    strategy_id: int
    tickers: List[PortfolioTickerWeight]
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
    allow_short: bool = False
    stop_loss_pct: Optional[float] = None
    take_profit_pct: Optional[float] = None
```

And update `run_portfolio_backtest_endpoint`'s call:

```python
    try:
        record = await create_pending_portfolio_backtest(
            req.strategy_id,
            [t.model_dump() for t in req.tickers],
            req.start_date,
            req.end_date,
            req.initial_capital,
            req.commission_pct,
            req.slippage_pct,
            db,
            user,
            allow_short=req.allow_short,
            stop_loss_pct=req.stop_loss_pct,
            take_profit_pct=req.take_profit_pct,
        )
    except ValueError as e:
        status_code = 403 if str(e) == "Unauthorized" else (404 if "not found" in str(e) else 400)
        raise HTTPException(status_code=status_code, detail=str(e))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd BackEnd && uv run pytest tests/test_portfolio_backtest_service.py -v`
Expected: PASS — all tests in the file, including the 3 new ones and every pre-existing test (positional `create_pending_portfolio_backtest(...)` call sites are unaffected since the new params are keyword-only).

- [ ] **Step 5: Run the full backend suite**

Run: `cd BackEnd && uv run pytest tests/ -v`
Expected: PASS — full suite green, confirming Tasks 1-6 haven't broken anything elsewhere (e.g. `tests/test_celery_tasks.py`, which drives `execute_backtest`/`execute_portfolio_backtest` end-to-end under `task_always_eager`).

- [ ] **Step 6: Commit**

```bash
git add BackEnd/services/portfolio_backtest_service.py BackEnd/routers/backtest.py BackEnd/tests/test_portfolio_backtest_service.py
git commit -m "Wire allow_short/stop_loss_pct/take_profit_pct through portfolio backtest API"
```

---

### Task 7: Frontend API layer

**Files:**
- Modify: `FrontEnd/src/api/backtest.js`

**Interfaces:**
- Produces: `runBacktest(strategyId, ticker, startDate, endDate, initialCapital, commissionPct, slippagePct, allowShort = false, stopLossPct = null, takeProfitPct = null)`; `runPortfolioBacktest(strategyId, tickers, startDate, endDate, initialCapital, commissionPct, slippagePct, allowShort = false, stopLossPct = null, takeProfitPct = null)` — both post `allow_short`/`stop_loss_pct`/`take_profit_pct` in the request body.

No backend test harness covers frontend JS in this repo (per CLAUDE.md, there is no frontend test suite) — this task is verified by Task 8's manual browser check, which exercises these functions end-to-end.

- [ ] **Step 1: Update `runBacktest` and `runPortfolioBacktest`**

Replace the top of `FrontEnd/src/api/backtest.js`:

```js
import client from './client.js';

export async function runBacktest(strategyId, ticker, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05, allowShort = false, stopLossPct = null, takeProfitPct = null) {
  const { data } = await client.post('/api/backtest/run', {
    strategy_id: strategyId,
    ticker,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
    allow_short: allowShort,
    stop_loss_pct: stopLossPct,
    take_profit_pct: takeProfitPct,
  });
  return data;
}
```

And `runPortfolioBacktest`:

```js
export async function runPortfolioBacktest(strategyId, tickers, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05, allowShort = false, stopLossPct = null, takeProfitPct = null) {
  const { data } = await client.post('/api/backtest/run-portfolio', {
    strategy_id: strategyId,
    tickers,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
    allow_short: allowShort,
    stop_loss_pct: stopLossPct,
    take_profit_pct: takeProfitPct,
  });
  return data;
}
```

Leave `getBacktestResults`, `getBacktestDetail`, `getPortfolioBacktestResults`, `getPortfolioBacktestDetail` unchanged.

- [ ] **Step 2: Commit**

```bash
git add FrontEnd/src/api/backtest.js
git commit -m "Add allow_short/stop_loss_pct/take_profit_pct params to backtest API client"
```

---

### Task 8: Frontend backtest form (`StrategiesPage.jsx`)

**Files:**
- Modify: `FrontEnd/src/pages/StrategiesPage.jsx`

**Interfaces:**
- Consumes: Task 7's updated `runBacktest`/`runPortfolioBacktest` signatures.

- [ ] **Step 1: Add form state**

In `FrontEnd/src/pages/StrategiesPage.jsx`, update the `backtestForm` initial state (line 39-44):

```jsx
  const [backtestForm, setBacktestForm] = useState({
    ticker: '',
    startDate: '2023-01-01',
    endDate: '2024-01-01',
    initialCapital: 10000,
    allowShort: false,
    stopLossPct: '',
    takeProfitPct: '',
  });
```

- [ ] **Step 2: Pass the new fields through submission**

Replace `handleRunBacktest` (lines 151-184):

```jsx
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

(`0.1`/`0.05` are passed explicitly for `commissionPct`/`slippagePct` since the form has no inputs for them and JS has no keyword args — this matches what the previous call already relied on implicitly via the functions' defaults.)

- [ ] **Step 3: Add form inputs**

In the `layout two-cols` div (right after the "Initial Capital" `<label className="field">` block, before its closing `</div>` at line 344), add:

```jsx
                <label className="field">
                  <span>
                    <input
                      type="checkbox"
                      checked={backtestForm.allowShort}
                      onChange={(e) => setBacktestForm({ ...backtestForm, allowShort: e.target.checked })}
                      style={{ marginRight: '6px' }}
                    />
                    Allow shorting
                  </span>
                </label>
                <label className="field">
                  <span>Stop Loss %</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    placeholder="Disabled"
                    value={backtestForm.stopLossPct}
                    onChange={(e) => setBacktestForm({ ...backtestForm, stopLossPct: e.target.value })}
                  />
                </label>
                <label className="field">
                  <span>Take Profit %</span>
                  <input
                    type="number"
                    min="0"
                    step="0.1"
                    placeholder="Disabled"
                    value={backtestForm.takeProfitPct}
                    onChange={(e) => setBacktestForm({ ...backtestForm, takeProfitPct: e.target.value })}
                  />
                </label>
```

- [ ] **Step 4: Manual browser verification**

Run: `cd FrontEnd && npm run dev` (and `cd BackEnd && uv run uvicorn app:app --reload` in another terminal, plus Redis/Celery worker per README if testing an actual run through to completion).

In the browser: open a project's Strategies page, select a strategy with market data available, check "Allow shorting", enter a Stop Loss % and Take Profit %, and submit both a single-ticker and a portfolio backtest. Confirm no console errors and that the request body (via browser devtools Network tab) includes `allow_short`/`stop_loss_pct`/`take_profit_pct` with the expected values.

- [ ] **Step 5: Commit**

```bash
git add FrontEnd/src/pages/StrategiesPage.jsx
git commit -m "Add allow shorting / stop loss / take profit inputs to backtest form"
```

---

### Task 9: Results page — direction badge and exit reason

**Files:**
- Modify: `FrontEnd/src/pages/BacktestResultsPage.jsx:209-223`
- Modify: `FrontEnd/src/styles.css:420-435`

**Interfaces:**
- Consumes: `trade.direction` (`'long' | 'short' | undefined`), `trade.exit_reason` (`'signal' | 'stop_loss' | 'take_profit' | undefined`) — these fields originate in Tasks 2-4's `_open_position`/`_close_position`, and reach this page via Task 5/6's API responses unchanged.

- [ ] **Step 1: Add direction/exit-reason badge styles**

In `FrontEnd/src/styles.css`, right after the existing `.badge.exit` rule (line 432-435), add:

```css
.badge.direction-long {
  background: rgba(93, 162, 255, 0.2);
  color: #5da2ff;
}

.badge.direction-short {
  background: rgba(255, 184, 108, 0.2);
  color: #ffb86c;
}
```

- [ ] **Step 2: Render the direction badge and exit reason**

In `FrontEnd/src/pages/BacktestResultsPage.jsx`, replace the trade-item rendering (lines 211-222):

```jsx
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
```

`trade.direction === 'short' ? 'short' : 'long'` treats older stored trades (no `direction` field) as long, matching the spec's backward-compatibility requirement.

- [ ] **Step 3: Manual browser verification**

With the dev server running (per Task 8 Step 4), open a backtest result that includes short trades and forced exits (from Task 8's manual test run). Confirm each trade row shows a LONG/SHORT badge and, for stop-loss/take-profit exits, "Stopped out"/"Took profit" text; confirm older pre-existing results (if any in local dev data) still render with a LONG badge and no crash.

- [ ] **Step 4: Commit**

```bash
git add FrontEnd/src/pages/BacktestResultsPage.jsx FrontEnd/src/styles.css
git commit -m "Show trade direction badge and stop-loss/take-profit exit reason in results"
```

---

### Task 10: Chart — direction-aware markers and same-bar multi-trade fix

**Files:**
- Modify: `FrontEnd/src/components/BacktestChart.jsx`

**Interfaces:**
- Consumes: `trade.direction`, same as Task 9.

`tradesByDate` currently is a single `Map` keyed by date holding one trade — with SL/TP-driven same-bar flips (Task 4), a single date can now have both an exit and an entry trade, and the old single-`Map` `forEach` silently drops one of them. This task splits it into two maps (by trade type) to fix that, alongside adding short-specific markers.

- [ ] **Step 1: Rewrite the trade-lookup and chart-data logic**

In `FrontEnd/src/components/BacktestChart.jsx`, replace lines 16-32:

```jsx
  const entryByDate = new Map();
  const exitByDate = new Map();
  (trades || []).forEach((t) => {
    if (t.type === 'entry') entryByDate.set(t.date, t);
    else if (t.type === 'exit') exitByDate.set(t.date, t);
  });

  const chartData = data.map((d) => {
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
```

- [ ] **Step 2: Replace the two `Scatter` series with four direction-aware ones**

Replace lines 70-86 (the two `<Scatter>` elements):

```jsx
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
```

- [ ] **Step 3: Manual browser verification**

With the dev server running, open the Price & Signals chart for a backtest result containing both long and short trades (from Task 8's manual run). Confirm 4 distinct legend entries appear (Long Entry, Short Entry, Long Exit, Short Exit) with visibly different marker shapes/colors, and — for a result with a same-bar stop-loss-then-reentry — confirm both markers render on that date instead of one silently overwriting the other.

- [ ] **Step 4: Commit**

```bash
git add FrontEnd/src/components/BacktestChart.jsx
git commit -m "Add direction-aware trade markers and fix same-date trade collision in BacktestChart"
```

---

## Self-Review Notes

- **Spec coverage:** signal semantics (Tasks 2-3), sizing (Task 2's `_open_position`), stop-loss/take-profit close-only checks + same-bar flip (Task 4), trade record additive fields (Tasks 2-4), API/data flow for both single-ticker and portfolio (Tasks 5-6), metrics (no changes needed — `_calculate_metrics` untouched, verified by Task 3/4's tests passing with existing metrics code), frontend (Tasks 7-10), testing (SQLite-only, no Postgres hand-verification needed — confirmed in spec, no new date/type-sensitive SQL introduced by any task here).
- **Type consistency:** `_open_position`/`_close_position` return signature `(direction, qty, entry_price, entry_basis, cash)` is identical across every call site in Tasks 2-4. `StrategyExecutor.backtest()`'s new kwargs (`allow_short`, `stop_loss_pct`, `take_profit_pct`) match the names used in `execute_backtest`/`execute_portfolio_backtest` (Tasks 5-6) and the frontend's snake_case request body keys (Task 7).
- **Keyword-only params:** confirmed via reading the actual test files that `create_pending_backtest` has one router call site + one existing test call site, and `create_pending_portfolio_backtest` has 7 existing positional call sites in `test_portfolio_backtest_service.py` alone — the keyword-only (`*,`) placement in Tasks 5-6 is what keeps all of those working without modification.

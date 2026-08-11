# Backtest Engine Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the broken signals/equity results pipeline, add capital-based position sizing with commissions/slippage, add max-drawdown and Sharpe-ratio metrics, and implement the MACD/EMA indicators already advertised in the UI but not wired up in `StrategyExecutor`.

**Architecture:** Extend `StrategyExecutor` (`BackEnd/services/strategy_executor.py`) in place — no new classes. Add two JSON columns to `BacktestResult` to persist the per-bar signal series and equity curve. Thread new cost parameters through `BacktestRequest` → `backtest_service.run_backtest` → `StrategyExecutor.backtest`. Update the frontend chart/results page to consume the corrected data.

**Tech Stack:** FastAPI, SQLAlchemy (async), pandas, numpy, pytest (new), React, Recharts.

## Global Constraints

- No new abstractions/classes for the engine — extend `StrategyExecutor` in place, following its existing small-private-method style.
- No DB migration tooling exists (`init_db()` only runs `create_all`). After Task 7, any local dev database needs its `backtest_results` table dropped/recreated to pick up the new columns.
- Sharpe ratio assumes daily bars and a 0% risk-free rate (documented simplification, not configurable in this phase).
- Position sizing is "all-in" — max whole shares affordable, no partial allocation, no concurrent/multiple positions.
- Commission/slippage are simple percentage-of-notional models, not tiered.
- No automated DB-touching or frontend tests are added in this phase (no existing fixtures/test runner for either) — those tasks are verified manually (curl / browser). Only `StrategyExecutor` gets automated `pytest` unit tests.
- `BackEnd/requirements.txt` does not exist yet; Task 1 creates it since it's required to install `pytest` reproducibly.

---

## Task 1: Backend test infrastructure + baseline indicator characterization tests

**Files:**
- Create: `BackEnd/requirements.txt`
- Create: `BackEnd/tests/__init__.py`
- Create: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: `StrategyExecutor` (`BackEnd/services/strategy_executor.py`) as it exists today — `_calculate_indicators(df, params)` mutating `df` in place.
- Produces: a working `pytest` setup later tasks add tests to; a fixture-style helper `make_price_df(closes)` in the test file that later tasks reuse.

- [ ] **Step 1: Create `BackEnd/requirements.txt`**

```
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
psycopg2-binary==2.9.9
python-dotenv==1.0.1
bcrypt==4.2.0
passlib==1.7.4
python-jose[cryptography]==3.3.0
pandas==2.2.2
numpy==1.26.4
pydantic[email]==2.9.2
python-multipart==0.0.9
pytest==8.3.3
```

- [ ] **Step 2: Create a virtualenv and install dependencies**

Run:
```bash
cd BackEnd
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```
Expected: install completes with no errors.

- [ ] **Step 3: Create the tests package**

```bash
mkdir -p BackEnd/tests
touch BackEnd/tests/__init__.py
```

- [ ] **Step 4: Write baseline characterization tests for the existing SMA, RSI, and Bollinger Bands indicators**

Create `BackEnd/tests/test_strategy_executor.py`:

```python
import pandas as pd
import pytest

from services.strategy_executor import StrategyExecutor


def make_price_df(closes):
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    df = pd.DataFrame({"close": closes}, index=dates)
    return df


def make_executor(parameters=None, entry="close > 0", exit="close < 0"):
    config = {
        "name": "test",
        "parameters": parameters or {},
        "rules": {"entry": entry, "exit": exit},
    }
    return StrategyExecutor(config)


def test_sma_matches_pandas_rolling_mean():
    closes = [10, 11, 12, 13, 14, 15, 16, 17]
    df = make_price_df(closes)
    executor = make_executor({"fast_ma": 3})
    executor._calculate_indicators(df, {"fast_ma": 3})
    expected = pd.Series(closes, index=df.index).rolling(window=3).mean()
    pd.testing.assert_series_equal(df["fast_ma"], expected, check_names=False)


def test_rsi_known_values():
    # Monotonically increasing closes -> RSI should approach 100 (no losses)
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
    df = make_price_df(closes)
    executor = make_executor({"rsi_period": 14})
    executor._calculate_indicators(df, {"rsi_period": 14})
    last_rsi = df["rsi"].iloc[-1]
    assert last_rsi == pytest.approx(100.0, abs=1e-6)


def test_bollinger_bands_match_manual_calc():
    closes = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19]
    df = make_price_df(closes)
    executor = make_executor({"bb_period": 5, "bb_std": 2})
    executor._calculate_indicators(df, {"bb_period": 5, "bb_std": 2})
    mid = pd.Series(closes, index=df.index).rolling(window=5).mean()
    std = pd.Series(closes, index=df.index).rolling(window=5).std()
    pd.testing.assert_series_equal(df["bb_mid"], mid, check_names=False)
    pd.testing.assert_series_equal(df["bb_upper"], mid + 2 * std, check_names=False)
    pd.testing.assert_series_equal(df["bb_lower"], mid - 2 * std, check_names=False)
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `cd BackEnd && source .venv/bin/activate && pytest tests/test_strategy_executor.py -v`
Expected: 3 passed (these characterize existing, already-correct behavior — no implementation change in this task).

- [ ] **Step 6: Commit**

```bash
git add BackEnd/requirements.txt BackEnd/tests/
git commit -m "test: add pytest infra and baseline indicator characterization tests"
```

---

## Task 2: Implement the EMA indicator

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_calculate_indicators`, add `_calculate_ema`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: `make_price_df`, `make_executor` from Task 1.
- Produces: `StrategyExecutor._calculate_ema(self, series: pd.Series, period: int) -> pd.Series`. `_calculate_indicators` now sets `df['ema']` when `params['ema_period']` is present.

- [ ] **Step 1: Write the failing test**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_ema_matches_pandas_ewm():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    df = make_price_df(closes)
    executor = make_executor({"ema_period": 4})
    executor._calculate_indicators(df, {"ema_period": 4})
    expected = pd.Series(closes, index=df.index).ewm(span=4, adjust=False).mean()
    pd.testing.assert_series_equal(df["ema"], expected, check_names=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_executor.py::test_ema_matches_pandas_ewm -v`
Expected: FAIL — `KeyError: 'ema'` (column not produced yet).

- [ ] **Step 3: Implement `_calculate_ema` and wire it into `_calculate_indicators`**

In `BackEnd/services/strategy_executor.py`, add a method on `StrategyExecutor` (place it near `_calculate_indicators`):

```python
    def _calculate_ema(self, series: pd.Series, period: int) -> pd.Series:
        """Exponential moving average, matching pandas' standard ewm formula"""
        return series.ewm(span=period, adjust=False).mean()
```

In `_calculate_indicators`, after the Bollinger Bands block, add:

```python
        # Exponential Moving Average
        if 'ema_period' in params:
            df['ema'] = self._calculate_ema(df['close'], int(params['ema_period']))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_executor.py::test_ema_matches_pandas_ewm -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "feat: implement EMA indicator in StrategyExecutor"
```

---

## Task 3: Implement the MACD indicator

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_calculate_indicators`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: `StrategyExecutor._calculate_ema` from Task 2.
- Produces: `_calculate_indicators` sets `df['macd']`, `df['macd_signal_line']`, `df['macd_hist']` when `params['macd_fast']`, `params['macd_slow']`, `params['macd_signal']` are all present — matching the params `StrategyBuilder.jsx` already sends.

- [ ] **Step 1: Write the failing test**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_macd_matches_manual_ema_calc():
    closes = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19, 21, 20, 22, 24, 23]
    df = make_price_df(closes)
    executor = make_executor({"macd_fast": 3, "macd_slow": 6, "macd_signal": 2})
    executor._calculate_indicators(df, {"macd_fast": 3, "macd_slow": 6, "macd_signal": 2})

    price = pd.Series(closes, index=df.index)
    fast_ema = price.ewm(span=3, adjust=False).mean()
    slow_ema = price.ewm(span=6, adjust=False).mean()
    expected_macd = fast_ema - slow_ema
    expected_signal = expected_macd.ewm(span=2, adjust=False).mean()
    expected_hist = expected_macd - expected_signal

    pd.testing.assert_series_equal(df["macd"], expected_macd, check_names=False)
    pd.testing.assert_series_equal(df["macd_signal_line"], expected_signal, check_names=False)
    pd.testing.assert_series_equal(df["macd_hist"], expected_hist, check_names=False)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_executor.py::test_macd_matches_manual_ema_calc -v`
Expected: FAIL — `KeyError: 'macd'`

- [ ] **Step 3: Implement MACD in `_calculate_indicators`**

In `BackEnd/services/strategy_executor.py`, after the EMA block added in Task 2:

```python
        # MACD
        if 'macd_fast' in params and 'macd_slow' in params and 'macd_signal' in params:
            fast_ema = self._calculate_ema(df['close'], int(params['macd_fast']))
            slow_ema = self._calculate_ema(df['close'], int(params['macd_slow']))
            df['macd'] = fast_ema - slow_ema
            df['macd_signal_line'] = self._calculate_ema(df['macd'], int(params['macd_signal']))
            df['macd_hist'] = df['macd'] - df['macd_signal_line']
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_executor.py::test_macd_matches_manual_ema_calc -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "feat: implement MACD indicator in StrategyExecutor"
```

---

## Task 4: Capital-based position sizing, commissions, and slippage in `_execute_trades`

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_execute_trades`, add `_format_date`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: a `df` with a `signal` column (0/1/-1) and `close` column, as produced by `backtest()`'s signal loop (unchanged).
- Produces: `StrategyExecutor._execute_trades(self, df: pd.DataFrame, initial_capital: float, commission_pct: float = 0.1, slippage_pct: float = 0.05) -> Tuple[List[Dict], List[Dict]]` returning `(trades, equity_curve)`. `equity_curve` is a list of `{'date': str, 'equity': float}`, one entry per bar. Trade dicts keep their existing shape (`type`, `price`, `date`, `size`, and `pnl` on exit) but `size` now reflects computed share count. Also produces `StrategyExecutor._format_date(self, idx) -> str`, a small helper extracted from the existing inline date-formatting ternary, reused by Task 5.

- [ ] **Step 1: Write the failing test**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_execute_trades_applies_capital_sizing_commission_and_slippage():
    closes = [100, 100, 110]
    df = make_price_df(closes)
    df["signal"] = [0, 1, -1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=1.0, slippage_pct=0.5
    )

    assert len(trades) == 2
    entry, exit_ = trades

    assert entry["type"] == "entry"
    assert entry["size"] == 9
    assert entry["price"] == pytest.approx(100.5, abs=1e-6)

    assert exit_["type"] == "exit"
    assert exit_["size"] == 9
    assert exit_["price"] == pytest.approx(109.45, abs=1e-6)
    assert exit_["pnl"] == pytest.approx(61.6545, abs=1e-3)

    assert len(equity_curve) == 3
    assert equity_curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert equity_curve[1]["equity"] == pytest.approx(986.455, abs=1e-3)
    assert equity_curve[2]["equity"] == pytest.approx(1061.6545, abs=1e-3)


def test_execute_trades_skips_entry_when_cash_cannot_afford_one_share():
    closes = [1000, 1000, 1100]
    df = make_price_df(closes)
    df["signal"] = [0, 1, -1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=500.0, commission_pct=1.0, slippage_pct=0.5
    )

    assert trades == []
    assert all(pt["equity"] == pytest.approx(500.0) for pt in equity_curve)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_executor.py::test_execute_trades_applies_capital_sizing_commission_and_slippage -v`
Expected: FAIL — current `_execute_trades` returns only `trades` (not a tuple) and ignores `commission_pct`/`slippage_pct`/`initial_capital` sizing, so the call signature/assertions mismatch.

- [ ] **Step 3: Rewrite `_execute_trades`**

In `BackEnd/services/strategy_executor.py`, replace the existing `_execute_trades` method body entirely, and add `_format_date` above it:

```python
    def _format_date(self, idx) -> str:
        return idx.isoformat() if hasattr(idx, 'isoformat') else str(idx)

    def _execute_trades(self, df: pd.DataFrame, initial_capital: float,
                         commission_pct: float = 0.1, slippage_pct: float = 0.05):
        """Execute trades based on signals using capital-based sizing with commission/slippage"""
        trades = []
        equity_curve = []
        cash = initial_capital
        shares = 0
        entry_cost_basis = 0.0

        for i in range(len(df)):
            signal = df.iloc[i].get('signal', 0)
            close_price = df.iloc[i]['close']
            timestamp = self._format_date(df.index[i])

            if signal == 1 and shares == 0:
                fill_price = close_price * (1 + slippage_pct / 100)
                effective_price = fill_price * (1 + commission_pct / 100)
                candidate_shares = int(cash // effective_price)
                if candidate_shares > 0:
                    commission = candidate_shares * fill_price * (commission_pct / 100)
                    cost = candidate_shares * fill_price + commission
                    shares = candidate_shares
                    cash -= cost
                    entry_cost_basis = cost
                    trades.append({
                        'type': 'entry',
                        'price': float(fill_price),
                        'date': timestamp,
                        'size': shares,
                    })

            elif signal == -1 and shares > 0:
                fill_price = close_price * (1 - slippage_pct / 100)
                proceeds = shares * fill_price
                commission = proceeds * (commission_pct / 100)
                net_proceeds = proceeds - commission
                pnl = net_proceeds - entry_cost_basis
                cash += net_proceeds
                trades.append({
                    'type': 'exit',
                    'price': float(fill_price),
                    'date': timestamp,
                    'size': shares,
                    'pnl': float(pnl),
                })
                shares = 0
                entry_cost_basis = 0.0

            equity = cash + (shares * close_price if shares > 0 else 0)
            equity_curve.append({'date': timestamp, 'equity': float(equity)})

        return trades, equity_curve
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_strategy_executor.py -k execute_trades -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "feat: capital-based position sizing with commission/slippage in _execute_trades"
```

---

## Task 5: Wire `backtest()` — cost params through, row-record signals, equity curve in output

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`backtest`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: `_execute_trades` returning `(trades, equity_curve)` from Task 4; `_format_date` from Task 4.
- Produces: `StrategyExecutor.backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0, commission_pct: float = 0.1, slippage_pct: float = 0.05) -> Dict[str, Any]` returning `{'trades': [...], 'metrics': {...}, 'signals': [{'date': str, 'close': float, 'signal': int}, ...], 'equity_curve': [...]}`. This is the shape `backtest_service.py` (Task 8) will persist.

- [ ] **Step 1: Write the failing test**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_backtest_returns_row_record_signals_and_equity_curve():
    closes = [100, 101, 99, 102, 105, 103, 108]
    df = make_price_df(closes)
    config = {
        "name": "sma-cross",
        "parameters": {"fast_ma": 2, "slow_ma": 4},
        "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
    }
    executor = StrategyExecutor(config)

    result = executor.backtest(df, initial_capital=1000.0, commission_pct=0.1, slippage_pct=0.05)

    assert set(result.keys()) == {"trades", "metrics", "signals", "equity_curve"}
    assert len(result["signals"]) == len(closes)
    assert len(result["equity_curve"]) == len(closes)
    for row in result["signals"]:
        assert set(row.keys()) == {"date", "close", "signal"}
    for row in result["equity_curve"]:
        assert set(row.keys()) == {"date", "equity"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_strategy_executor.py::test_backtest_returns_row_record_signals_and_equity_curve -v`
Expected: FAIL — `backtest()` doesn't accept `commission_pct`/`slippage_pct` yet and returns the old columnar `signals` dict with no `equity_curve` key.

- [ ] **Step 3: Update `backtest()`**

In `BackEnd/services/strategy_executor.py`, replace the `backtest` method body:

```python
    def backtest(self, df: pd.DataFrame, initial_capital: float = 10000.0,
                 commission_pct: float = 0.1, slippage_pct: float = 0.05) -> Dict[str, Any]:
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
        params = self.config.get('parameters', {})
        rules = self.config.get('rules', {})

        self._calculate_indicators(df, params)

        df['signal'] = 0

        for i in range(1, len(df)):
            if self._evaluate_condition(rules['entry'], df, i):
                df.loc[i, 'signal'] = 1
            elif self._evaluate_condition(rules['exit'], df, i):
                df.loc[i, 'signal'] = -1

        trades, equity_curve = self._execute_trades(df, initial_capital, commission_pct, slippage_pct)
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
```

Note: this passes `equity_curve` into `_calculate_metrics`, which Task 6 will update to accept it — until Task 6 lands, `_calculate_metrics`'s signature won't match. Complete Task 6 immediately after this step before running the full test file (the single test in this task's Step 4 only exercises `backtest()`, so run it as scoped below; don't run the full suite until Task 6 is also done).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_strategy_executor.py::test_backtest_returns_row_record_signals_and_equity_curve -v`
Expected: FAIL at this point with a `TypeError` from `_calculate_metrics` missing the `equity_curve` argument — this is expected and resolved by Task 6. Do not commit until Task 6's Step 4 passes; treat Tasks 5 and 6 as landing together in Task 6's commit. Proceed directly to Task 6.

---

## Task 6: Max drawdown and Sharpe ratio metrics

**Files:**
- Modify: `BackEnd/services/strategy_executor.py` (`_calculate_metrics`)
- Test: `BackEnd/tests/test_strategy_executor.py`

**Interfaces:**
- Consumes: `equity_curve: List[Dict[str, Any]]` (`{'date', 'equity'}`) from Task 4/5.
- Produces: `StrategyExecutor._calculate_metrics(self, df: pd.DataFrame, trades: List[Dict], initial_capital: float, equity_curve: List[Dict]) -> Dict[str, Any]`, adding `max_drawdown_pct` and `sharpe_ratio` to both the trades-present and no-trades-early-return branches, alongside the existing `total_return`/`return_pct`/`win_rate`/`num_trades`/`final_capital`.

- [ ] **Step 1: Write the failing tests**

Append to `BackEnd/tests/test_strategy_executor.py`:

```python
def test_max_drawdown_pct_known_curve():
    equity_values = [1000, 1050, 1020, 1100, 950, 1080]
    equity_curve = [{"date": str(i), "equity": v} for i, v in enumerate(equity_values)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df(equity_values), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    expected_drawdown = (1100 - 950) / 1100 * 100
    assert metrics["max_drawdown_pct"] == pytest.approx(expected_drawdown, rel=1e-6)


def test_sharpe_ratio_matches_manual_formula():
    equity_values = [1000, 1050, 1020, 1100, 950, 1080]
    equity_curve = [{"date": str(i), "equity": v} for i, v in enumerate(equity_values)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df(equity_values), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    returns = pd.Series(equity_values).pct_change().dropna()
    expected_sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_sharpe, rel=1e-6)


def test_sharpe_ratio_zero_when_no_volatility():
    equity_curve = [{"date": str(i), "equity": 1000.0} for i in range(5)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df([1000] * 5), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    assert metrics["sharpe_ratio"] == 0.0
    assert metrics["max_drawdown_pct"] == 0.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_strategy_executor.py -k "drawdown or sharpe" -v`
Expected: FAIL — `_calculate_metrics` doesn't accept `equity_curve` yet and doesn't return these keys.

- [ ] **Step 3: Update `_calculate_metrics`**

In `BackEnd/services/strategy_executor.py`, replace the `_calculate_metrics` method:

```python
    def _calculate_metrics(self, df: pd.DataFrame, trades: List[Dict],
                            initial_capital: float, equity_curve: List[Dict]) -> Dict[str, Any]:
        """Calculate performance metrics"""
        max_drawdown_pct = self._max_drawdown_pct(equity_curve)
        sharpe_ratio = self._sharpe_ratio(equity_curve)

        if not trades:
            return {
                'total_return': 0.0,
                'return_pct': 0.0,
                'win_rate': 0.0,
                'num_trades': 0,
                'max_drawdown_pct': max_drawdown_pct,
                'sharpe_ratio': sharpe_ratio,
            }

        total_pnl = sum(t.get('pnl', 0) for t in trades if t['type'] == 'exit')

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
            'max_drawdown_pct': max_drawdown_pct,
            'sharpe_ratio': sharpe_ratio,
        }

    def _max_drawdown_pct(self, equity_curve: List[Dict]) -> float:
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

    def _sharpe_ratio(self, equity_curve: List[Dict]) -> float:
        if len(equity_curve) < 2:
            return 0.0
        values = pd.Series([p['equity'] for p in equity_curve])
        returns = values.pct_change().dropna()
        std = returns.std()
        if not std or pd.isna(std) or std == 0:
            return 0.0
        return float((returns.mean() / std) * (252 ** 0.5))
```

- [ ] **Step 4: Run the full test file to verify everything passes**

Run: `pytest tests/test_strategy_executor.py -v`
Expected: all tests pass (this also confirms Task 5's `backtest()` now works end-to-end, since `_calculate_metrics` finally accepts `equity_curve`).

- [ ] **Step 5: Commit both Task 5 and Task 6's changes together**

```bash
git add BackEnd/services/strategy_executor.py BackEnd/tests/test_strategy_executor.py
git commit -m "feat: wire equity curve/signals through backtest() and add drawdown/Sharpe metrics"
```

---

## Task 7: Persist `signals` and `equity_curve` on `BacktestResult`

**Files:**
- Modify: `BackEnd/database/models.py:62-73` (`BacktestResult`)

**Interfaces:**
- Produces: `BacktestResult.signals` (JSON column, default `[]`) and `BacktestResult.equity_curve` (JSON column, default `[]`), matching the shapes `StrategyExecutor.backtest()` now returns (Task 5).

- [ ] **Step 1: Add the two columns**

In `BackEnd/database/models.py`, in the `BacktestResult` class, after the existing `trades` column:

```python
    trades = Column(JSON, default=[])  # List of trades executed, each with details (entry/exit, price, size)
    signals = Column(JSON, default=[])  # Per-bar {date, close, signal} series for charting
    equity_curve = Column(JSON, default=[])  # Per-bar {date, equity} mark-to-market series
    logs = Column(Text, default='')  # Optional logs or error messages
```

- [ ] **Step 2: Verify the columns are registered on the model**

Run:
```bash
cd BackEnd && source .venv/bin/activate
python -c "from database.models import BacktestResult; print(sorted(BacktestResult.__table__.columns.keys()))"
```
Expected: output list includes `'signals'` and `'equity_curve'`.

- [ ] **Step 3: Recreate the local dev database table** (no migration tooling exists — `create_all` won't alter an existing table)

If you have a local Postgres `quanttrade` database already running with a `backtest_results` table, drop it so `init_db()` recreates it with the new columns on next app startup:
```bash
psql postgresql://postgres:postgres@localhost/quanttrade -c "DROP TABLE IF EXISTS backtest_results CASCADE;"
```
Expected: table dropped; it's recreated automatically the next time `uvicorn app:app` starts (via the `startup_event` → `init_db()` → `create_all`).

- [ ] **Step 4: Commit**

```bash
git add BackEnd/database/models.py
git commit -m "feat: add signals and equity_curve columns to BacktestResult"
```

---

## Task 8: Thread cost params through the API and persist the new fields

**Files:**
- Modify: `BackEnd/routers/backtest.py` (`BacktestRequest`, `run_backtest_endpoint`, `get_backtest_detail`)
- Modify: `BackEnd/services/backtest_service.py` (`run_backtest`)

**Interfaces:**
- Consumes: `StrategyExecutor.backtest(df, initial_capital, commission_pct, slippage_pct)` from Task 5, `BacktestResult.signals`/`equity_curve` columns from Task 7.
- Produces: `POST /api/backtest/run` accepts optional `commission_pct`/`slippage_pct` in the request body (defaults 0.1/0.05) and its response includes `signals`/`equity_curve`. `GET /api/backtest/{backtest_id}` returns the real stored `signals`/`equity_curve` instead of faking `signals` from `trades`.

- [ ] **Step 1: Add cost params to `BacktestRequest`**

In `BackEnd/routers/backtest.py`, update the model:

```python
class BacktestRequest(BaseModel):
    strategy_id: int
    ticker: str
    start_date: str
    end_date: str
    initial_capital: float = 10000.0
    commission_pct: float = 0.1
    slippage_pct: float = 0.05
```

- [ ] **Step 2: Pass the new fields through the run endpoint**

In `BackEnd/routers/backtest.py`, update `run_backtest_endpoint`:

```python
@router.post("/run")
async def run_backtest_endpoint(
    req: BacktestRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user)
):
    """Run a backtest for a strategy"""
    return await run_backtest(
        req.strategy_id,
        req.ticker,
        req.start_date,
        req.end_date,
        req.initial_capital,
        req.commission_pct,
        req.slippage_pct,
        db,
        user
    )
```

- [ ] **Step 3: Update `run_backtest` in `backtest_service.py` to accept and forward the cost params, and persist the new fields**

In `BackEnd/services/backtest_service.py`, update the signature and body:

```python
async def run_backtest(strategy_id: int, ticker: str, start_date: str, end_date: str,
                       initial_capital: float = 10000.0,
                       commission_pct: float = 0.1,
                       slippage_pct: float = 0.05,
                       db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    """Run backtest for a strategy on market data"""

    strategy_result = await db.execute(
        select(Strategy).where(Strategy.id == strategy_id)
    )
    strategy = strategy_result.scalars().first()
    if strategy is None:
        raise HTTPException(status_code=404, detail="Strategy not found")

    if strategy.project.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Unauthorized")

    data_result = await db.execute(
        select(MarketData).where(
            MarketData.ticker == ticker,
            MarketData.date >= start_date,
            MarketData.date <= end_date
        ).order_by(MarketData.date)
    )
    market_data_rows = data_result.scalars().all()
    if not market_data_rows:
        raise HTTPException(status_code=404, detail="No market data found in the specified date range")

    data_dicts = [
        {
            'date': row.date,
            'open': float(row.open),
            'high': float(row.high),
            'low': float(row.low),
            'close': float(row.close),
            'volume': float(row.volume),
        }
        for row in market_data_rows
    ]
    df = pd.DataFrame(data_dicts)
    df.set_index('date', inplace=True)

    try:
        executor = StrategyExecutor(strategy.parameters)
        backtest_results = executor.backtest(
            df, initial_capital=initial_capital,
            commission_pct=commission_pct, slippage_pct=slippage_pct
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Backtest execution failed: {str(e)}")

    result_record = BacktestResult(
        strategy_id=strategy_id,
        start_date=datetime.fromisoformat(start_date),
        end_date=datetime.fromisoformat(end_date),
        results=backtest_results['metrics'],
        trades=backtest_results['trades'],
        signals=backtest_results['signals'],
        equity_curve=backtest_results['equity_curve'],
    )

    db.add(result_record)
    await db.commit()
    await db.refresh(result_record)

    return {
        'id': result_record.id,
        'strategy_id': strategy_id,
        'metrics': backtest_results['metrics'],
        'trades': backtest_results['trades'],
        'signals': backtest_results['signals'],
        'equity_curve': backtest_results['equity_curve'],
        'created_at': result_record.created_at.isoformat(),
    }
```

- [ ] **Step 4: Fix `get_backtest_detail` to return the real stored fields**

In `BackEnd/routers/backtest.py`, update the return statement in `get_backtest_detail`:

```python
    return {
        'id': backtest.id,
        'strategy_id': backtest.strategy_id,
        'metrics': backtest.results,
        'trades': backtest.trades,
        'signals': backtest.signals,
        'equity_curve': backtest.equity_curve,
        'created_at': backtest.created_at.isoformat(),
    }
```

- [ ] **Step 5: Manually verify end-to-end via curl**

With the backend running (`uvicorn app:app --reload` from `BackEnd/`) and at least one uploaded `MarketData` row and one `Strategy` for a test user:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/token \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=<test-email>&password=<test-password>" | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -s -X POST http://localhost:8000/api/backtest/run \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"strategy_id": 1, "ticker": "TEST", "start_date": "2024-01-01", "end_date": "2024-06-01", "initial_capital": 1000, "commission_pct": 0.1, "slippage_pct": 0.05}' \
  | python3 -m json.tool
```
Expected: JSON response includes non-empty `signals` (list of `{date, close, signal}`) and `equity_curve` (list of `{date, equity}`) arrays the same length as the uploaded market data range, plus `metrics.max_drawdown_pct` and `metrics.sharpe_ratio`.

Then verify the detail endpoint returns the same data:
```bash
curl -s http://localhost:8000/api/backtest/<id-from-previous-response> \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```
Expected: `signals` and `equity_curve` present and non-empty (not duplicating `trades`).

- [ ] **Step 6: Commit**

```bash
git add BackEnd/routers/backtest.py BackEnd/services/backtest_service.py
git commit -m "feat: thread commission/slippage through the backtest API and persist signals/equity_curve"
```

---

## Task 9: Frontend API client — cost params on `runBacktest`

**Files:**
- Modify: `FrontEnd/src/api/backtest.js`

**Interfaces:**
- Produces: `runBacktest(strategyId, ticker, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05)`.

- [ ] **Step 1: Update `runBacktest`**

```javascript
import client from './client.js';

export async function runBacktest(strategyId, ticker, startDate, endDate, initialCapital = 10000, commissionPct = 0.1, slippagePct = 0.05) {
  const { data } = await client.post('/api/backtest/run', {
    strategy_id: strategyId,
    ticker,
    start_date: startDate,
    end_date: endDate,
    initial_capital: initialCapital,
    commission_pct: commissionPct,
    slippage_pct: slippagePct,
  });
  return data;
}

export async function getBacktestResults(strategyId) {
  const { data } = await client.get(`/api/backtest/results/${strategyId}`);
  return data;
}

export async function getBacktestDetail(backtestId) {
  const { data } = await client.get(`/api/backtest/${backtestId}`);
  return data;
}
```

- [ ] **Step 2: Verify the frontend still builds**

Run: `cd FrontEnd && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add FrontEnd/src/api/backtest.js
git commit -m "feat: add commission/slippage params to runBacktest client"
```

---

## Task 10: Fix `BacktestChart.jsx` — date-based trade matching + equity curve panel

**Files:**
- Modify: `FrontEnd/src/components/BacktestChart.jsx`

**Interfaces:**
- Consumes: `data` (array of `{date, close, signal}`, i.e. `signals` from the API), `trades` (array of `{type, price, date, size, pnl?}`), new prop `equityCurve` (array of `{date, equity}`).
- Produces: `BacktestChart({ data, trades, equityCurve })` — renders the price/signal chart with correctly matched entry/exit markers, plus a separate equity curve chart below it.

- [ ] **Step 1: Rewrite the component**

```jsx
import React from 'react';
import {
  LineChart, Line, ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, Legend, ResponsiveContainer, ComposedChart
} from 'recharts';

export default function BacktestChart({ data, trades, equityCurve }) {
  if (!data || data.length === 0) {
    return <p className="muted">No data to display</p>;
  }

  const tradesByDate = new Map();
  (trades || []).forEach((t) => {
    tradesByDate.set(t.date, t);
  });

  const chartData = data.map((d) => {
    const trade = tradesByDate.get(d.date);
    const entry = trade && trade.type === 'entry' ? trade : null;
    const exit = trade && trade.type === 'exit' ? trade : null;
    return {
      date: d.date,
      close: d.close,
      entry: entry ? entry.price : null,
      exit: exit ? exit.price : null,
      pnl: exit ? exit.pnl : null,
    };
  });

  const equityData = (equityCurve || []).map((point) => ({
    date: point.date,
    equity: point.equity,
  }));

  return (
    <div className="chart-container">
      <ResponsiveContainer width="100%" height={400}>
        <ComposedChart data={chartData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 12 }}
            interval={Math.floor(chartData.length / 10)}
          />
          <YAxis yAxisId="left" />
          <YAxis yAxisId="right" orientation="right" />
          <Tooltip
            formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)}
          />
          <Legend />

          <Line
            yAxisId="left"
            type="monotone"
            dataKey="close"
            stroke="#5da2ff"
            name="Stock Price"
            dot={false}
            isAnimationActive={false}
          />

          <Scatter
            yAxisId="left"
            dataKey="entry"
            fill="#7cf2d4"
            name="Entry (Buy)"
            shape="triangle"
            isAnimationActive={false}
          />

          <Scatter
            yAxisId="left"
            dataKey="exit"
            fill="#ff6b6b"
            name="Exit (Sell)"
            shape="diamond"
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>

      {equityData.length > 0 && (
        <ResponsiveContainer width="100%" height={200}>
          <LineChart data={equityData} margin={{ top: 5, right: 30, left: 0, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" />
            <XAxis
              dataKey="date"
              tick={{ fontSize: 12 }}
              interval={Math.floor(equityData.length / 10)}
            />
            <YAxis />
            <Tooltip formatter={(value) => (typeof value === 'number' ? value.toFixed(2) : value)} />
            <Legend />
            <Line
              type="monotone"
              dataKey="equity"
              stroke="#c792ea"
              name="Equity"
              dot={false}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Verify the frontend still builds**

Run: `cd FrontEnd && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 3: Commit**

```bash
git add FrontEnd/src/components/BacktestChart.jsx
git commit -m "fix: match trade markers by date and add equity curve panel to BacktestChart"
```

---

## Task 11: Wire `BacktestResultsPage.jsx` to the corrected data and new metrics

**Files:**
- Modify: `FrontEnd/src/pages/BacktestResultsPage.jsx`

**Interfaces:**
- Consumes: `BacktestChart({ data, trades, equityCurve })` from Task 10; `selectedResult.signals`, `selectedResult.equity_curve`, `selectedResult.metrics.max_drawdown_pct`, `selectedResult.metrics.sharpe_ratio` from Task 8's API response.

- [ ] **Step 1: Add the two new metric tiles**

In `FrontEnd/src/pages/BacktestResultsPage.jsx`, inside the `metrics-grid` div, after the existing "Trades" metric:

```jsx
                <div className="metric">
                  <span className="label">Max Drawdown</span>
                  <span className="value">{selectedResult.metrics?.max_drawdown_pct?.toFixed(2) || 0}%</span>
                </div>
                <div className="metric">
                  <span className="label">Sharpe Ratio</span>
                  <span className="value">{selectedResult.metrics?.sharpe_ratio?.toFixed(2) || 0}</span>
                </div>
```

- [ ] **Step 2: Fix the chart data wiring**

Replace the closing `{/* Chart */}` block:

```jsx
      {selectedResult && (
        <div className="card" style={{ marginTop: '20px' }}>
          <h3>Price & Signals</h3>
          <BacktestChart
            data={selectedResult.signals}
            trades={selectedResult.trades}
            equityCurve={selectedResult.equity_curve}
          />
        </div>
      )}
```

- [ ] **Step 3: Verify the frontend still builds**

Run: `cd FrontEnd && npm run build`
Expected: build succeeds with no errors.

- [ ] **Step 4: Commit**

```bash
git add FrontEnd/src/pages/BacktestResultsPage.jsx
git commit -m "fix: wire BacktestResultsPage to real signals/equity_curve and show drawdown/Sharpe"
```

---

## Task 12: End-to-end manual verification

**Files:** none (verification only)

**Interfaces:** none — this exercises the full stack built by Tasks 1-11.

- [ ] **Step 1: Start the backend**

```bash
cd BackEnd && source .venv/bin/activate && uvicorn app:app --reload
```
Expected: starts without error; startup log shows tables created (including the new columns from Task 7).

- [ ] **Step 2: Start the frontend**

```bash
cd FrontEnd && npm run dev
```
Expected: Vite dev server starts, app reachable at `http://localhost:5173`.

- [ ] **Step 3: Walk the golden path in the browser**

1. Register/log in.
2. Create a project, then a strategy using the "Moving Average Crossover" template in the Strategy Builder (or one using MACD, to exercise Task 3).
3. Upload a handful of `MarketData` rows for a ticker spanning at least 20+ dates (via `POST /api/data/upload`, since there's no bulk-upload UI yet — out of scope for this phase).
4. Run a backtest for that strategy/ticker/date range from the UI.
5. On the results page, confirm: the price line renders with real close prices, entry/exit triangle and diamond markers appear at the correct dates, the equity curve panel renders below it, and the Max Drawdown / Sharpe Ratio tiles show non-placeholder numbers.

Expected: no console errors, all of the above renders correctly with real data (not the previous broken/empty state).

- [ ] **Step 4: Confirm the full backend test suite passes one more time**

Run: `cd BackEnd && source .venv/bin/activate && pytest tests/ -v`
Expected: all tests from Tasks 1-6 pass.

No commit for this task — it's verification only.

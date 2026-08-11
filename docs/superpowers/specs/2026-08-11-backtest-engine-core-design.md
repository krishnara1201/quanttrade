# Backtest Engine Core: Design

## Context

QuantTrade's backtesting engine (`StrategyExecutor` in `BackEnd/services/strategy_executor.py`) is currently a minimal proof of concept: it trades a fixed 1 share per signal, applies no commissions or slippage, computes only four basic metrics, and two of the four indicators advertised in the UI (`MACD`, and `EMA` is never offered but MACD is) aren't actually implemented — selecting MACD in the Strategy Builder silently produces a strategy that can't reference `macd`-derived columns.

Separately, the results pipeline that's supposed to feed the frontend chart is broken: `StrategyExecutor.backtest()` computes a `signals` series (per-bar close price + buy/sell signal), but `services/backtest_service.py` never persists it on `BacktestResult`, and `routers/backtest.py`'s detail endpoint fakes the `signals` field in its response by re-using `trades`. The frontend (`BacktestChart.jsx`) then tries to match trade markers to chart rows via a `trade.index` field that trade records don't have (they only carry `date`), so entry/exit markers never render even when data is present.

This is phase 1 of turning QuantTrade into a genuinely useful backtesting app. It focuses on making the core engine and results pipeline correct and realistic. Follow-up phases (not covered here): market data ingestion (bulk import / external data fetch), and further execution realism (shorting, stop-loss/take-profit, walk-forward optimization).

Goal: after this phase, a user can build a strategy using any of the five advertised indicators, run a backtest with realistic capital-based position sizing and trading costs, and see accurate performance metrics (including drawdown and Sharpe) plus a correctly rendered price/signal/equity chart.

## Non-goals

- Market data ingestion improvements (still manual single-row upload via `POST /api/data/upload`) — separate phase.
- Short selling, stop-loss/take-profit, multi-asset/portfolio backtests, parameter optimization — separate phase.
- Fixing the unrelated in-memory rate limiter or router prefix inconsistency (`/strategies` vs `/api/strategies`) — out of scope, not touched by this work.

## Data model changes

`BackEnd/database/models.py`, `BacktestResult`: add two nullable JSON columns:

- `signals`: list of `{date, close, signal}` records, one per bar (`signal` is 0/1/-1 as today). Replaces the current fake `signals` field that routers/backtest.py synthesizes from `trades`.
- `equity_curve`: list of `{date, equity}` records — mark-to-market portfolio value (cash + position value) at each bar.

No new tables, no ORM relationship changes. **Caveat:** the project has no migration tooling — `database/connection.py`'s `init_db()` only runs `Base.metadata.create_all`, which does not alter existing tables. Any existing local dev database needs its `backtest_results` table dropped (or the DB recreated) after this change for the new columns to appear.

## Execution engine (`services/strategy_executor.py`)

### Indicators

Add `_calculate_ema` (pandas `.ewm(span=period, adjust=False).mean()`) and extend `_calculate_indicators` to compute MACD when `macd_fast`/`macd_slow`/`macd_signal` params are present:
- `macd = ema(close, fast) - ema(close, slow)`
- `macd_signal_line = ema(macd, signal)`
- `macd_hist = macd - macd_signal_line`

This matches the params `StrategyBuilder.jsx` already sends (`macd_fast`/`macd_slow`/`macd_signal`, defaults 12/26/9) — today they're accepted by the API and silently ignored by the engine.

### Position sizing & costs

`BacktestRequest` (`routers/backtest.py`) gains two optional fields, run-level because they model market/broker conditions rather than strategy logic:
- `commission_pct: float = 0.1` (percent of trade notional)
- `slippage_pct: float = 0.05` (percent applied against the trader on fill)

Rewrite `_execute_trades` to track running `cash` (starting at `initial_capital`) instead of a fixed 1-share position:

- **Entry**: `fill_price = close * (1 + slippage_pct/100)`; `shares = floor(cash / fill_price)`; if `shares == 0`, treat as no trade (skip — consistent with existing "condition not met" semantics, not an error); `commission = shares * fill_price * commission_pct/100`; `cash -= shares*fill_price + commission`.
- **Exit**: `fill_price = close * (1 - slippage_pct/100)`; `proceeds = shares * fill_price`; `commission = proceeds * commission_pct/100`; `cash += proceeds - commission`; `pnl = proceeds - commission - (entry cost basis)`.
- **Equity tracking**: at every bar, append `{date, equity: cash + (shares*close if in position else 0)}` to the equity curve list.

Trade records keep their existing shape (`type`, `price`, `date`, `size`, `pnl` on exit) but `size` now reflects the computed share count instead of a hardcoded `1`.

### Metrics

Extend `_calculate_metrics` (keeping existing `total_return`, `return_pct`, `win_rate`, `num_trades`, `final_capital`):

- `max_drawdown_pct`: largest peak-to-trough percentage decline over the equity curve.
- `sharpe_ratio`: `mean(bar_returns) / stddev(bar_returns) * sqrt(252)`, computed from bar-over-bar equity curve returns. Assumes daily bars and a 0% risk-free rate — documented as a known simplification since `MarketData` has no explicit timeframe field.

`backtest()`'s return shape changes: `signals` becomes a list of row-records (`[{date, close, signal}, ...]`) instead of the current columnar `{'close': [...], 'signal': [...]}` dict, and a new `equity_curve` key is added alongside `trades`/`metrics`.

## API changes

- `services/backtest_service.py::run_backtest`: pass `commission_pct`/`slippage_pct` through to `StrategyExecutor.backtest()`; persist `signals` and `equity_curve` on the created `BacktestResult` row; include both in the endpoint's JSON response.
- `routers/backtest.py::get_backtest_detail`: return the stored `signals`/`equity_curve` directly instead of synthesizing `signals` from `trades`.
- `routers/backtest.py::BacktestRequest`: add `commission_pct`/`slippage_pct` optional fields (defaults as above).

## Frontend changes

- `FrontEnd/src/api/backtest.js`: `runBacktest` accepts optional `commissionPct`/`slippagePct` and includes them in the POST body.
- `FrontEnd/src/components/BacktestChart.jsx`: match trade markers to chart rows by `date` (fixing the current dead `trade.index` lookup — trades never carried an `index` field), plot the real price/signal line from `signals`, and add an equity curve line/panel fed by `equity_curve`.
- `FrontEnd/src/pages/BacktestResultsPage.jsx`: pass `selectedResult.signals`/`equity_curve` directly to `BacktestChart` (drop the current `.map((d, idx) => ({...d, index: idx}))` workaround), and add `max_drawdown_pct`/`sharpe_ratio` tiles to the metrics grid.

## Error handling

Unchanged pattern: `StrategyExecutor` raises `ValueError` on invalid config, caught in `run_backtest` and surfaced as HTTP 400 (`services/backtest_service.py` already does this). Insufficient cash to buy even one share on entry is not an error — it's treated the same as any other unmet entry condition (no trade that bar).

## Testing

No test suite exists anywhere in the repo today. This phase adds `pytest` (+ `pytest-asyncio` for any async DB-touching tests, though the engine unit tests themselves are synchronous) and covers `StrategyExecutor` directly:

- Indicator correctness: SMA/EMA/RSI/Bollinger Bands/MACD computed against known values on a small fixed price series.
- Position sizing & costs: given a synthetic price series and known commission/slippage, assert resulting share counts, cash balance, and trade PnL.
- Metrics: max drawdown and Sharpe ratio computed against a synthetic equity curve with a known answer.

Manual/end-to-end verification: run a backtest through the UI (Strategy Builder → run backtest → results page) and confirm the price/signal chart and equity curve render real data, and that entry/exit markers align with the trades list.

## Open assumptions to revisit later

- Sharpe ratio assumes daily bars; if intraday data is ever supported, the annualization factor needs to change.
- Position sizing is "all-in" (max whole shares affordable) — no partial-capital allocation or multiple concurrent positions.
- Commission/slippage are simple percentage models, not tiered or per-broker-specific.

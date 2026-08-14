# Shorting + Stop-Loss/Take-Profit — Design

## Summary

Extend the backtest engine's execution layer (`StrategyExecutor._execute_trades`) to support short positions and per-run stop-loss/take-profit exits, gated behind new optional, per-backtest-run parameters. The engine is currently long-only, one position at a time, with exits driven purely by strategy signal (`signal == -1`). This adds:

- An `allow_short` flag that reinterprets `signal == -1` as "open a short" (instead of "exit only") when the account is flat.
- `stop_loss_pct` / `take_profit_pct`, checked against each bar's close, that force an exit independent of the strategy's own signal.

Both are **per backtest run** (like `commission_pct`/`slippage_pct` today), not saved on the strategy — the same strategy can be re-run with different risk settings without editing it. This applies uniformly to rules-mode and custom-code strategies, since the reinterpretation happens entirely in the execution layer, after signal generation.

Explicitly out of scope for this pass: ATR-based (volatility-adjusted) stops, intrabar high/low-based stop triggering, multi-position/pyramiding, margin/leverage modeling.

## Architecture

### Signal semantics

No change to how rules-mode or custom-code strategies *produce* signals — both still emit `{1, -1, 0}` per bar, unchanged. The reinterpretation lives entirely in `_execute_trades`:

- `allow_short=False` (default): byte-identical to current behavior. `signal=1` opens long if flat; `signal=-1` closes a long if one's open; otherwise no-op.
- `allow_short=True`: `signal=1` while short covers the short and, same bar, can then open a new long; `signal=-1` while flat opens a short; `signal=-1` while long still closes the long (unchanged); `signal=1` while flat still opens a long (unchanged). Note this cover-then-reopen behavior is new — today's `signal` column encodes at most one action per bar (an entry *or* an exit, never both), so no same-bar transition of any kind currently exists to mirror.

### Position state

`_execute_trades` currently tracks `shares` (always ≥0, implicitly long) and `entry_cost_basis`. Replace with an explicit `position` dict (or equivalent local state): `{'direction': 'long'|'short'|None, 'qty': int, 'entry_price': float}`, tracked alongside `cash`. `qty` is always a positive count of shares; `direction` disambiguates.

### Sizing

Both long entries and short entries reuse today's all-in capital-based formula: `qty = cash // effective_price`, where `effective_price` folds in commission the same way the current long-entry path does. Opening a short **credits** cash with `qty * fill_price - commission` (as if selling borrowed shares); covering **debits** cash with `qty * fill_price + commission`. No margin/collateral requirement is modeled — the short's "buying power" is simply the same cash-based affordability check used for longs today, which is a deliberate simplification consistent with this engine's existing no-leverage design.

PnL on a short exit: `entry_proceeds - cover_cost` (mirrors the existing long PnL of `exit_proceeds - entry_cost`).

### Stop-loss / take-profit

New optional `stop_loss_pct: float | None` and `take_profit_pct: float | None` (both `None` = disabled, the default). Checked **once per bar, before signal evaluation**, against the bar's `close` only (no intrabar high/low):

- Long position: stop triggers if `close <= entry_price * (1 - stop_loss_pct/100)`; take-profit triggers if `close >= entry_price * (1 + take_profit_pct/100)`.
- Short position: stop triggers if `close >= entry_price * (1 + stop_loss_pct/100)`; take-profit triggers if `close <= entry_price * (1 - take_profit_pct/100)`.

If either triggers, force an exit at that bar's close (same fill-price/commission/slippage math as a signal-driven exit), tag the trade `exit_reason: "stop_loss"` or `"take_profit"`. The bar's own signal is still evaluated afterward in the same iteration — if it indicates a new entry, that entry can still open same-bar. This is new behavior: today a bar's `signal` value encodes at most one action (an entry or an exit, never both), so no same-bar exit-then-entry sequence is currently possible: decoupling the forced SL/TP exit from the signal is what makes it possible here. If both stop and take-profit would technically trigger on the same bar (only possible with degenerate/zero pct values), check stop-loss first.

Signal-driven exits get `exit_reason: "signal"` for consistency (new field, not currently present).

### Trade record shape

Additive fields only — existing consumers reading `type`/`price`/`date`/`size`/`pnl` are unaffected:
- Entry trades gain `direction: "long" | "short"`.
- Exit trades gain `exit_reason: "signal" | "stop_loss" | "take_profit"`.

Previously-stored `BacktestResult.trades` JSON (pre-this-change) has neither field — any frontend rendering must treat their absence as `direction: "long"` / no reason shown, not error.

### Metrics

No changes to `_calculate_metrics`. `pnl` on each exit trade already carries the correct sign once `_execute_trades` computes it correctly for shorts (negative when a short loses money, i.e. price rose), so `win_rate`, `total_return`, `return_pct`, `final_capital`, `max_drawdown_pct`, and `sharpe_ratio` all continue to work unmodified — they only ever read `pnl`/the equity curve, never `direction`.

## API (`routers/backtest.py`)

`BacktestRequest` and `PortfolioBacktestRequest` each gain:
```python
allow_short: bool = False
stop_loss_pct: Optional[float] = None
take_profit_pct: Optional[float] = None
```
Threaded through the existing call chains, same pattern as `commission_pct`/`slippage_pct`:
- `create_pending_backtest(...)` (`services/backtest_service.py`) → stored on `BacktestResult` (new nullable columns `allow_short`, `stop_loss_pct`, `take_profit_pct`) → `execute_backtest` reads them off the row → `StrategyExecutor.backtest(df, ..., allow_short=..., stop_loss_pct=..., take_profit_pct=...)`.
- `create_pending_portfolio_backtest(...)` (`services/portfolio_backtest_service.py`) → same three new nullable columns on `PortfolioBacktestResult` → `execute_portfolio_backtest` passes them to each per-ticker `StrategyExecutor.backtest()` call unchanged (no new aggregation logic needed — this is orthogonal to weight/equity-curve aggregation).

`StrategyExecutor.backtest()`'s signature gains `allow_short: bool = False, stop_loss_pct: Optional[float] = None, take_profit_pct: Optional[float] = None`, passed straight through to `_execute_trades`.

Validation: `stop_loss_pct`/`take_profit_pct`, if provided, must be `> 0` (a `0` or negative value would trigger every bar or never trigger meaningfully) — reject with a 400 at the router level (Pydantic `Field(gt=0)` with `Optional`, or a manual check) rather than letting it silently produce degenerate backtests.

## Data model (`database/models.py`)

`BacktestResult` and `PortfolioBacktestResult` each gain:
```python
allow_short = Column(Boolean, default=False, nullable=False)
stop_loss_pct = Column(Float, nullable=True)
take_profit_pct = Column(Float, nullable=True)
```
Purely additive columns on existing tables — per this project's no-migration-tooling convention (`init_db()` only runs `create_all`), any pre-existing local/dev database needs these two tables dropped and recreated (or `docker compose down -v`) before the new columns are usable, same as prior schema changes documented in CLAUDE.md.

## Frontend

- **`src/api/backtest.js`**: `runBacktest`/`runPortfolioBacktest` gain three more optional trailing params (`allowShort=false, stopLossPct=null, takeProfitPct=null`), appended to the existing request body alongside `commission_pct`/`slippage_pct`.
- **`StrategiesPage.jsx`**: backtest form (`backtestForm` state) gains `allowShort` (checkbox), `stopLossPct`, `takeProfitPct` (optional numeric inputs, blank = disabled/`null`). Wired into both `handleRunBacktest` submit paths (single-ticker and portfolio), matching the existing `initialCapital` input's pattern.
- **`BacktestResultsPage.jsx`**: `.trade-item` rendering adds a direction badge (`Long`/`Short`, styled distinctly, defaulting to `Long` when `direction` is absent from older results) next to the existing entry/exit badge, and for exits, a human-readable `exit_reason` label ("Stopped out" / "Took profit" / omitted for `"signal"` or missing).
- **`BacktestChart.jsx`**: entry/exit scatter markers get a direction-aware variant — short entries/exits use a distinct marker color or shape from long ones (e.g. long entry stays the existing teal triangle; short entry uses an inverted triangle or a different color) so a short trade doesn't visually read as a long one. Falls back to the current long styling when `direction` is absent.

## Testing

Extend `tests/test_strategy_executor.py` with hand-verified cases (no mocks, matching existing style):
- `allow_short=False` regression: existing test cases produce byte-identical output (guards against accidentally changing default behavior).
- Short entry → short exit via signal: PnL sign and cash accounting verified by hand for a simple up/down price sequence.
- Stop-loss triggers on a long position mid-sequence: verify forced exit at the correct bar/price, `exit_reason="stop_loss"`, and that a later signal can still re-enter.
- Take-profit triggers on a short position: verify forced exit and correct PnL sign.
- Same-bar flip: stop-loss exit followed immediately by a new entry signal on the same bar.
- Validation: `stop_loss_pct=0` or negative rejected with 400 at the router/service level.

No new Postgres-dialect-sensitive surface (no new date/type-sensitive SQL comparisons — new columns are `Boolean`/`Float`, not date-filtered), so SQLite-based tests are sufficient per this repo's existing dialect-sensitivity guidance; the boolean/float column additions don't need a live-Postgres hand-verification pass the way date-filtering fixes have in the past.

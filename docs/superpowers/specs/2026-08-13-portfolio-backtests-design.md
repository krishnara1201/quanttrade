# Portfolio-Level Backtests — Design

## Summary

Add a "Portfolio" backtest mode alongside the existing single-ticker backtest. A portfolio backtest runs one strategy (rules-based or custom-code) independently across a user-chosen basket of tickers, each funded from a custom, fixed weight of the initial capital. Results are aggregated into a portfolio-level equity curve and metrics, alongside a per-ticker breakdown.

This is intentionally scoped to **basket diversification**, not cross-ticker strategy logic:
- No rebalancing — weights are applied once at the start; each ticker's sub-account then trades independently for the whole run.
- No cross-ticker conditions (e.g. pairs trading, rotation) — the same single-ticker strategy definition runs unmodified against each ticker in the basket.
- No shorting or concurrent multi-position sizing beyond what the existing engine already supports per ticker (long-only, one position at a time, per sub-account).

## Architecture

Each ticker in the basket gets its own capital sub-account: `sub_capital = initial_capital * normalized_weight`. Each sub-account is run through the **existing, unmodified** `StrategyExecutor.backtest()` — same rules/custom-code strategy, same `_execute_trades`/`_calculate_metrics`. A new `services/portfolio_backtest_service.py` orchestrates:

1. Validate the request (≥2 tickers, all weights > 0).
2. Validate every ticker has full data coverage over `[start_date, end_date]` (reusing the same range-check data `routers/data.py`'s `GET /{ticker}/range` already exposes — min date, max date, count). If any ticker fails this, reject with a 400 naming the offending ticker and its actual available range, rather than silently running on partial data.
3. Normalize weights to sum to 1.0 (e.g. `{AAPL: 2, MSFT: 1}` → `{AAPL: 0.667, MSFT: 0.333}`), so the user doesn't have to hand-compute percentages that add to exactly 100.
4. For each ticker: load its `MarketData` into a DataFrame (same query shape as `backtest_service.run_backtest`), run `StrategyExecutor(strategy.parameters, code=strategy.code).backtest(df, initial_capital=sub_capital, commission_pct=..., slippage_pct=...)`.
5. Aggregate: build the portfolio equity curve as the date-union of all per-ticker equity curves, forward-filling any ticker's last-known equity on dates where that ticker's own curve doesn't have an entry (this is a fallback for bar-level gaps within an otherwise-covered range — the coverage check in step 2 guards against gross missing ranges, not necessarily every single bar). Sum equity across tickers per date to get the portfolio total.
6. Compute pooled metrics from the aggregate curve and the concatenated trade lists (see Metrics below).

**Refactor for reuse:** `_max_drawdown_pct` and `_sharpe_ratio` in `strategy_executor.py` don't reference `self` — pull them out as module-level functions in `strategy_executor.py` (or a small shared `metrics.py`) so `portfolio_backtest_service.py` can compute the same drawdown/Sharpe math on the aggregate curve without duplicating it. `StrategyExecutor` keeps thin wrapper methods (or calls the module functions directly) so the existing single-ticker code path and its tests are unaffected.

## Data model

New table `PortfolioBacktestResult` (`database/models.py`), kept separate from `BacktestResult` so the existing single-ticker table/endpoints/tests are untouched:

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
    results = Column(JSON, default={})       # aggregate metrics, see below
    equity_curve = Column(JSON, default=[])  # aggregate portfolio {date, equity} series
    per_ticker = Column(JSON, default={})    # {ticker: {allocated_capital, metrics, trades, signals, equity_curve}}
    created_at = Column(DateTime, default=datetime.utcnow)
```

`Strategy` gets a second relationship, `portfolio_backtests`, alongside its existing `backtests` (`cascade="all, delete-orphan"`, same as `backtests`). `Float` isn't currently imported in `database/models.py` (existing columns use `Integer`/`String`/`DateTime`/`Text`/`Boolean`/`JSON`) — add it to the `sqlalchemy` import.

`per_ticker[ticker]` has the exact same shape as a single-ticker backtest's `{metrics, trades, signals, equity_curve}` (plus `allocated_capital`), so the existing `BacktestChart.jsx` can render any one ticker's slice with no changes.

No migration tooling exists in this project (`init_db()` only runs `Base.metadata.create_all`) — the local dev DB needs its tables dropped/recreated manually after this change, same as any other schema change here.

### Metrics (aggregate, in `results`)

- `final_capital`: last value of the aggregate equity curve.
- `total_return`: `final_capital - initial_capital`.
- `return_pct`: `total_return / initial_capital * 100`.
- `num_trades`: count of `exit` trades across all tickers' trade lists combined.
- `win_rate`: pooled — wins / total exits across all tickers combined (not averaged per-ticker).
- `max_drawdown_pct`, `sharpe_ratio`: computed on the aggregate equity curve via the extracted module-level functions.

## Backend API (`routers/backtest.py`)

- `POST /api/backtest/run-portfolio`
  Request body: `{strategy_id, tickers: [{ticker, weight}], start_date, end_date, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05}`.
  Same ownership check as today (`strategy.project.owner_id != user.id` → 403), same `selectinload(Strategy.project)` eager-load pattern to avoid the documented `MissingGreenlet` regression. Same `datetime.fromisoformat` parsing of `start_date`/`end_date` up front (documented Postgres date-vs-string pitfall) before it reaches a SQLAlchemy comparison.
- `GET /api/backtest/portfolio/results/{strategy_id}` — list summary rows (id, allocations, top-level metrics, created_at) for a strategy, same ownership check.
- `GET /api/backtest/portfolio/{id}` — full detail including `per_ticker`, same ownership check via `selectinload(PortfolioBacktestResult.strategy).selectinload(Strategy.project)`.

## Error handling

- Fewer than 2 tickers, or any weight ≤ 0 → 400, "Portfolio backtest requires at least 2 tickers with positive weights."
- Any ticker missing full coverage of `[start_date, end_date]` → 400 naming the ticker and its actual available range, e.g. `"AAPL has data from 2020-01-01 to 2022-06-01, which does not cover the requested 2019-01-01 to 2023-01-01."`
- A per-ticker `StrategyExecutor.backtest()` failure (e.g. sandboxed custom code raising a `SandboxError`, or a `ValueError` from validation) → 400 identifying which ticker failed, e.g. `"Backtest execution failed for MSFT: <message>"`, so a basket run doesn't fail opaquely on one bad ticker.

## Frontend

`StrategiesPage.jsx` backtest form gets a "Single ticker / Portfolio" toggle. Portfolio mode replaces the single ticker `<select>` with repeatable rows — ticker dropdown (sourced from the existing `GET /api/data/tickers`) + weight number input, with add/remove-row controls. No per-ticker date-range constraint UI beyond what the backend already validates and reports on submit.

`BacktestResultsPage.jsx` lists both single-ticker and portfolio runs in one list, portfolio rows badged e.g. "Portfolio · 3 tickers". Opening a portfolio result shows:
- The aggregate equity curve, via the existing `BacktestChart.jsx` fed the aggregate `equity_curve`.
- A per-ticker breakdown table (ticker, weight, return %, num trades, win rate), each row expandable to that ticker's own `BacktestChart.jsx` view fed `per_ticker[ticker]`.

## Testing

- `tests/test_portfolio_backtest_service.py` (new, no mocks, in-memory `sqlite+aiosqlite` for DB-touching cases, matching the existing test style):
  - Weight normalization (e.g. `{2, 1}` → `{0.667, 0.333}`).
  - Full-coverage rejection: a ticker with a gap or shorter range than requested → 400 naming that ticker.
  - Aggregate equity-curve math against hand-verified expected values, including the forward-fill behavior for a bar-level gap within an otherwise-covered range.
  - Pooled `win_rate`/`num_trades` across tickers with a mix of winning/losing trades.
  - Ownership-check regression mirroring `tests/test_backtest_ownership.py` (eager-loading via `selectinload`, so a plain `select()` without `.options(...)` fails the suite the same way).
- Extend `tests/test_strategy_executor.py`-adjacent coverage only if the `_max_drawdown_pct`/`_sharpe_ratio` extraction changes their call signature — otherwise the existing single-ticker tests should pass unmodified since `StrategyExecutor`'s public behavior doesn't change.

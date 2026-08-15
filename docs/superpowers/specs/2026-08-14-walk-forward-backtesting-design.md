# Walk-Forward Backtesting + Benchmark Overlay — Design

## Summary

Add a third backtest mode, **walk-forward evaluation**, scoped to `custom_code` (ML) strategies only. Instead of fitting/predicting once over the entire requested date range (today's behavior, which lets a model implicitly "see" the whole history before being scored on any of it), walk-forward splits the range into expanding train/test folds, re-runs signal generation fresh each fold, and stitches the out-of-sample (OOS) results into one continuous equity curve with capital compounding across folds. Alongside this, add a **buy-and-hold benchmark overlay** to every equity chart in the app (single-ticker, portfolio, and walk-forward), not just walk-forward results — it's cheap to compute from data already loaded and gives every backtest result a "did this even beat holding the stock" reference point.

This is intentionally scoped to:
- **Custom-code strategies only.** Rules-based strategies don't "fit" anything, so there's no leakage risk to guard against; walk-forward there would just be N independent re-backtests, which isn't what this spec is building. Rejected with a 400 if attempted.
- **Simple (non-strict) leakage boundary.** Each fold's `generate_signals(df)` call sees the *entire* train+test slice (not just train rows) — this prevents leakage *across folds* (a fold never sees future folds' data) but does not prevent a model from fitting on rows that fall inside its own fold's test window. A stricter no-leakage contract (`generate_signals(df, train_end_idx)`) was considered and rejected as a separate, larger spec — see Future Work.
- **No pre-trained/uploaded models.** Considered and explicitly deferred — see Future Work.

## Architecture

### Fold computation

Given `[start_date, end_date]` and a user-chosen `test_window_days`:
- Initial train window = `max(365 days, 0.25 * (end_date - start_date))`, anchored at `start_date`.
- From the end of the initial train window, step forward in `test_window_days` increments; each step is one fold's test window, with that fold's train window being everything from `start_date` through the start of its test window (expanding).
- A trailing remainder shorter than `test_window_days` is dropped, not turned into a short partial fold.
- Fewer than 1 full fold fits in the range → `ValueError("date range too short for the requested test window")`.

### Per-fold execution

Today, `StrategyExecutor.backtest()` bundles three steps into one call: generate signals → `_execute_trades` → `_calculate_metrics`, over one `df`. Walk-forward needs to split step 1 from step 2, so `strategy_executor.py` gets a small **refactor**: the existing signal-generation logic (both the rules-mode regex evaluator and the custom-code sandbox call) is extracted out of `backtest()` into a new method, `generate_signals(df) -> pd.Series`. `backtest()` is behavior-unchanged — it just calls this method internally instead of inlining the logic, so the existing single-ticker/portfolio test suite passing unmodified is the regression check that this refactor is behavior-preserving. No changes to `sandbox_executor.py` or the sandbox worker — each fold just hands it a shorter `df` than a full-range run would.

Per fold, `services/walk_forward_service.py` does:
1. `slice = df` from `start_date` through this fold's `test_end` (train+test rows).
2. `signals = executor.generate_signals(slice)`.
3. Truncate both `slice` and `signals` down to just `[test_start, test_end]`.
4. `executor._execute_trades(test_slice, test_signals, initial_capital=running_capital, commission_pct=..., slippage_pct=..., allow_short=..., stop_loss_pct=..., take_profit_pct=...)` — reusing the same private method the rules/custom-code paths already share.
5. `running_capital` = that fold's ending equity (last row of its equity segment), carried into the next fold — **capital compounds across folds**, producing one continuous OOS equity curve rather than N independent mini-backtests.
6. Fold-local `return_pct`/`num_trades` computed from that fold's own equity/trade segment, for the per-fold breakdown table.

Trades and equity rows from every fold are concatenated in chronological order into the result's `trades`/`equity_curve`; each `equity_curve` row is tagged with its `fold_index` so the frontend can shade fold boundaries.

### Benchmark overlay

For any backtest result (single-ticker, portfolio aggregate, or walk-forward), compute a buy-and-hold equity curve from the same `MarketData` rows already loaded: `shares = initial_capital / first_bar.close`, then `equity[t] = shares * bar[t].close` for every bar in the result's date range. No new data dependency — reuses OHLCV already queried for the backtest itself.

### Progress visibility

A walk-forward run means N sequential sandboxed subprocess calls inside one Celery task — a long date range with a small test window could take several minutes even though no single fold times out. To avoid the frontend showing an opaque spinner for that whole time:
- `WalkForwardBacktestResult` gets `total_folds` (set once, right after fold boundaries are computed, before the loop starts) and `folds_completed` (starts at 0, incremented and committed after **each** fold finishes — a small partial commit distinct from the final result write).
- The existing poll-the-row pattern (`GET /walk-forward/{id}`) now returns `{status: "running", folds_completed: 3, total_folds: 8}` instead of a bare status string — no WebSocket/SSE needed, just richer data on each poll.
- The Celery task's time limit is sized per-call rather than using the global default: the router estimates a worst-case fold count from `(end_date - start_date) / test_window_days` at enqueue time (deliberately not subtracting the initial train window, so this slightly overestimates fold count — erring toward a longer, safer time limit rather than a tight one that could clip a real run) and calls `walk_forward_task.apply_async(args=[row.id], time_limit=estimate, soft_time_limit=estimate - 30)` instead of `.delay(...)` — the only one of the four-plus-this task types that needs a per-call override.

## Data model

New table `WalkForwardBacktestResult` (`database/models.py`), kept separate from `BacktestResult`/`PortfolioBacktestResult` so existing tables/endpoints/tests are untouched — same precedent as the portfolio feature:

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
    allow_short = Column(Boolean, default=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    total_folds = Column(Integer, nullable=True)      # set once fold boundaries are computed
    folds_completed = Column(Integer, default=0)       # incremented per fold, for progress polling
    folds = Column(JSON, default=[])                   # [{fold_index, train_start, train_end, test_start, test_end, return_pct, num_trades}]
    trades = Column(JSON, default=[])                  # pooled across all fold test windows, chronological
    equity_curve = Column(JSON, default=[])             # stitched OOS curve, each row tagged fold_index
    benchmark_equity_curve = Column(JSON, default=[])   # buy-and-hold over the same stitched period
    results = Column(JSON, default={})                  # aggregate metrics, same field names as BacktestResult.results
    status = Column(String, default="pending")
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
```

`Strategy` gets a third relationship, `walk_forward_backtests`, alongside `backtests`/`portfolio_backtests` (`cascade="all, delete-orphan"`, same as the other two).

No migration tooling exists in this project — same manual drop/recreate note as every prior schema change here.

### Metrics (aggregate, in `results`)

Same field names as `BacktestResult.results` (`total_return`, `return_pct`, `final_capital`, `win_rate`, `num_trades`, `max_drawdown_pct`, `sharpe_ratio`), computed on the full stitched OOS curve via the same module-level `max_drawdown_pct`/`sharpe_ratio` functions already shared with the portfolio path — so the frontend's existing `metrics-grid` markup renders this with no changes.

## Backend API (`routers/backtest.py`)

- `POST /api/backtest/run-walk-forward`
  Request body: `{strategy_id, ticker, start_date, end_date, test_window_days, initial_capital=10000.0, commission_pct=0.1, slippage_pct=0.05, allow_short=false, stop_loss_pct=null, take_profit_pct=null}`.
  1. Eager-load `strategy.project` via `selectinload`, 403 if `strategy.project.owner_id != user.id`.
  2. 400 if `strategy.parameters["mode"] != "custom_code"`: `"Walk-forward evaluation requires a custom-code strategy."`
  3. Estimate worst-case fold count, create the pending row, commit.
  4. `apply_async(..., time_limit=..., soft_time_limit=...)` wrapped in try/except → on failure, `status="failed"` + `error_message`, commit, raise `HTTPException(503, "Task queue unavailable, please try again")` — same shape as the existing four `.delay()` guards.
- `GET /api/backtest/walk-forward/results/{strategy_id}` — list summary rows (id, ticker, test_window_days, status, folds_completed/total_folds, top-level metrics, created_at), same ownership check.
- `GET /api/backtest/walk-forward/{id}` — full detail including `folds`/`trades`/`equity_curve`/`benchmark_equity_curve`, same ownership check via `selectinload(WalkForwardBacktestResult.strategy).selectinload(Strategy.project)`.

## Task (`tasks.py`, `services/walk_forward_service.py`)

`walk_forward_task` is a thin sync wrapper running `execute_walk_forward` via `asyncio.run(...)`, matching the existing four tasks' shape exactly. `execute_walk_forward` follows the same "never raises" contract as the other four `execute_*` functions:

- Loads `MarketData` for `[start_date, end_date]`; empty → mark failed, don't raise (matches the portfolio service's empty-rows guard).
- Computes fold boundaries; fewer than 1 full fold → mark failed with a clear message, don't raise.
- Sets `total_folds`, commits.
- Per-fold loop: any exception from a fold's `StrategyExecutor`/sandbox call (`SandboxTimeoutError`/`SandboxMemoryError`/`SandboxRuntimeError`/`SandboxOutputError`/`ValueError`) fails the **entire** run — no partial-success state, since a stitched curve with a gap isn't meaningful. `error_message` names which fold failed and why, e.g. `"Fold 4/8 failed: SandboxTimeoutError: ..."`.
- Each successful fold: append trades/equity rows, bump `folds_completed`, commit (partial progress commit, distinct from the final write).
- After the loop: aggregate `results`, compute `benchmark_equity_curve`, `status="success"`, final commit.
- Catch-all `except Exception`: `await db.rollback()` (a DB-level failure can leave the session mid-failed-transaction), `status="failed"`, `error_message=f"{type(e).__name__}: {e}"`, commit — identical pattern to the other four `execute_*` functions, including the reasoning for `rollback()` before commit and for using `f"{type(e).__name__}: {e}"` over bare `str(e)`.

## Frontend

**`StrategiesPage.jsx`** — the existing "Single ticker / Portfolio" backtest-mode toggle gains a third option, "Walk-forward," shown only when the selected strategy's `parameters.mode === "custom_code"`. Reuses the existing ticker `<select>`, date-range inputs, `initial_capital`, `commission_pct`/`slippage_pct`, `allow_short`, `stop_loss_pct`/`take_profit_pct` fields as-is, plus one new field: test-window length (`<select>` of 3/6/12 months).

A collapsible "Example strategy for walk-forward" section sits under the Walk-forward toggle — a read-only code block (not an editable/loadable template; custom code itself is authored in `StrategyBuilder.jsx`, which this form doesn't touch) demonstrating the one non-obvious requirement: `generate_signals` must refit cleanly from whatever `df` it's handed, since the orchestrator calls it fresh once per fold with a growing slice.

```python
from sklearn.linear_model import LogisticRegression

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
    return signal
```

Deliberately avoids `import pandas`/`import numpy` (both are in the sandbox's disallowed-imports list — only the curated `sklearn` submodules are exempted) and uses only `df`'s own methods, matching what the sandbox actually permits.

**`BacktestResultsPage.jsx`** — walk-forward rows join the existing merged single-ticker/portfolio list, badged `"Walk-forward · N folds"`. While `status="running"`, the row shows `folds_completed/total_folds` (e.g. "Fold 3/8") instead of a bare spinner. The detail view adds:
- The stitched OOS equity curve via the existing `BacktestChart.jsx`, `priceName="Walk-Forward OOS Equity"`, plus the new `benchmarkEquityCurve` prop.
- Aggregate metrics via the existing `metrics-grid` markup (field names match, so it's a drop-in reuse).
- An expandable per-fold table (fold #, train range, test range, fold return %, trade count) — same expand/collapse pattern the portfolio view already uses for its per-ticker breakdown.

**`BacktestChart.jsx`** — one new optional prop, `benchmarkEquityCurve` (default `[]`, same opt-in pattern as the existing `priceName` prop), rendered as a second line in the equity panel. This prop is wired up for single-ticker and portfolio results too, not just walk-forward — every equity chart in the app gets the buy-and-hold overlay.

No new components, no new charting library — everything reuses existing pieces the same way the portfolio feature reused `BacktestChart` unmodified.

## Error handling

- Strategy not in `custom_code` mode → 400, `"Walk-forward evaluation requires a custom-code strategy."`
- Date range too short for even 1 fold → task fails with `status="failed"`, `error_message="date range too short for the requested test window"` (not a 400 at request time, since fold computation happens inside the async task, not the router — matches the "ownership check before enqueue, everything else inside the task" split this codebase already uses).
- No `MarketData` for the ticker/range → task fails, `error_message` naming the ticker/range, mirroring the portfolio service's empty-rows guard.
- A fold's sandbox execution failing → task fails, `error_message` names the fold index and the underlying exception; no partial-success state.
- `apply_async(...)` raising (broker unreachable) → row marked `failed` synchronously in the router, `HTTPException(503, "Task queue unavailable, please try again")` — same pattern as the other four enqueue call sites.

## Testing

- `tests/test_walk_forward_service.py` (new, no mocks, in-memory `sqlite+aiosqlite`, matching existing test style):
  - Fold-boundary math: short range (< 1 fold → `ValueError`), exact/inexact divisibility by `test_window_days`, remainder handling.
  - Capital compounding across folds against hand-verified expected values.
  - Stitched `trades`/`equity_curve` correctness and `fold_index` tagging.
  - Rules-mode strategy rejection.
  - Empty-`MarketData` guard.
  - `folds_completed`/`total_folds` progress updates across the loop.
  - Ownership-check regression mirroring `tests/test_backtest_ownership.py` (eager-loading via `selectinload`).
  - One true end-to-end run: a real sandboxed logistic-regression strategy (the example above) across multiple folds, asserting the final aggregate `results` shape.
- `tests/test_strategy_executor.py` — extended to confirm the `generate_signals` extraction is behavior-preserving (existing `backtest()` output unchanged for both rules and custom-code modes) — the existing suite passing unmodified is most of this check; add one direct test of `generate_signals` in isolation for both modes.
- `tests/test_celery_tasks.py` — extended with the new task, including the `apply_async(time_limit=...)` sizing and the 503-on-enqueue-failure path.
- Benchmark overlay: a focused test on the buy-and-hold equity curve calculation (`shares = initial_capital / first_close`, `equity[t] = shares * close[t]`) against hand-verified values, plus confirming it's computed identically regardless of which of the three result types it's attached to.

## Future work (explicitly deferred, not part of this spec)

- **Strict train/test separation.** A `generate_signals(df, train_end_idx=None)` contract (backward compatible — `None` preserves today's single-shot behavior) where walk-forward mode passes the fold's train/test boundary and expects user code to fit only on `df.iloc[:train_end_idx]`. Rejected for this spec as a larger lift (sandbox worker changes, doc/example updates, every existing custom-code strategy would need updating to be walk-forward-aware) relative to the simpler boundary this spec ships with.
- **Pre-trained models.** Two directions, neither included here:
  - User-uploaded `joblib`/pickle model artifacts — real risk: `pickle`/`joblib.load()` can execute arbitrary code during deserialization, which completely bypasses `check_ast_safety()`'s static AST checks (those protect against malicious *source code*, not deserialization payloads). Needs its own threat-model discussion before being built, even though this app's sandbox is already accepted as single-tenant/personal-use rather than hardened multi-tenant.
  - An app-shipped curated pretrained model (e.g. a FinBERT-style sentiment feature usable inside `generate_signals`) — no upload, no pickle risk, just a new safe import added to the sandbox's allowlist the same way the curated `sklearn` submodules were added.

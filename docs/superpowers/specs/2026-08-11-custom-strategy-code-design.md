# Custom Python Strategy Code (Sandboxed)

## Context

QuantTrade's only way to define a strategy today is `StrategyBuilder.jsx`'s visual builder: a fixed set of indicators (SMA, EMA, RSI, Bollinger Bands, MACD) and entry/exit condition strings evaluated by `StrategyExecutor._evaluate_condition` — a hand-rolled, non-`eval` regex parser that only supports a single comparison per condition (no boolean combinators, no "crossed above" logic referencing prior bars). This is deliberately safe but limited.

We want to let users write real Python to compute entry/exit signals, while keeping the same safety bar the existing condition parser holds today (CLAUDE.md is explicit that no arbitrary expression may reach `eval`/`exec` unchecked). This spec covers only that feature — an async job queue (Celery/Redis) for long-running backtests, richer risk controls (stop-loss/take-profit/position sizing %), and benchmark comparison were discussed and explicitly deferred to later work.

## Goal

Let a user submit a Python function that receives OHLCV data and returns a signal series, execute it safely, and feed the result into the existing (unchanged) trade-execution and metrics pipeline.

## Signal contract

User code must define exactly one top-level function:

```python
def generate_signals(df: pd.DataFrame) -> pd.Series:
    ...
```

- `df` has the same columns already available to the rule-based path today: `open`, `high`, `low`, `close`, `volume`, indexed by date.
- Return value must be a `pd.Series` aligned to `df.index`, values in `{1, -1, 0}` (entry / exit / hold) — the same semantics `StrategyExecutor.backtest()` already assigns via `df['signal']`.
- `pd` and `np` are provided as sandbox globals; `import pandas as pd` / `import numpy as np` also work via a restricted `__import__`.
- NaN in the returned series (e.g. rolling-window warm-up) is coerced to `0`.

User code's *only* responsibility is producing this column. Position sizing, commission/slippage, and metrics remain entirely in `_execute_trades`/`_calculate_metrics`, unchanged — this keeps the untrusted-code blast radius minimal and reuses code that's already tested.

## Sandbox design

Two new files, no new dependencies (`ast`, `subprocess`, `resource`, `tempfile`, `json`, `csv` are all stdlib):

### `BackEnd/services/sandbox_executor.py`

Exception hierarchy: `SandboxError` base, with `SandboxValidationError` (AST/syntax, pre-spawn), `SandboxTimeoutError`, `SandboxMemoryError`, `SandboxRuntimeError` (exception inside user's `generate_signals`), `SandboxOutputError` (bad return shape/values).

`check_ast_safety(code: str) -> None` — hand-rolled `ast.NodeVisitor`, matching this codebase's existing whitelist-not-eval style (`_evaluate_condition`) rather than pulling in a new dependency like RestrictedPython:
- `ast.parse` first; `SyntaxError` → `SandboxValidationError`.
- Requires a top-level `def generate_signals(...)`.
- Rejects `Import`/`ImportFrom` outside `{"pandas", "numpy"}`.
- Rejects calls to `eval`, `exec`, `compile`, `__import__`, `open`, `input`, `globals`, `locals`, `vars`, `exit`, `quit`.
- Rejects any identifier starting and ending with `__` (blocks `().__class__.__bases__`-style escapes).
- Collects all violations in one pass for useful save-time feedback.

`run_custom_strategy(code, df, *, timeout_s=10, mem_limit_mb=512, cpu_limit_s=8) -> pd.Series`:
1. Runs `check_ast_safety` — no subprocess spawned on failure.
2. Writes `code` and `df` (as CSV — trusted data, and avoids adding `pyarrow` for parquet or ever pickling untrusted code) to a temp directory.
3. Spawns `sys.executable _sandbox_worker.py <code> <input.csv> <output.json>` via `subprocess.run(..., timeout=timeout_s, preexec_fn=<sets RLIMIT_AS/RLIMIT_CPU>)`.
4. Translates the outcome: `TimeoutExpired` → `SandboxTimeoutError`; a clean `{"error_type": ..., "message": ...}` in `output.json` → the matching `SandboxError` subclass (includes the user's actual exception text, e.g. `ZeroDivisionError: division by zero`); a JSON array → success, coerced to `pd.Series(..., index=df.index, dtype=int)`; a killed process with no valid output → `SandboxMemoryError` (fallback path — the common OOM case is caught as a Python `MemoryError` inside the child and reported cleanly).
5. Always cleans up the temp directory.

`mem_limit_mb`'s default must be validated empirically against this repo's actual `.venv` pandas/numpy import RSS (~100-150MB observed baseline elsewhere) with 3-4x headroom, so small legitimate strategies don't spuriously hit the ceiling.

### `BackEnd/services/_sandbox_worker.py`

Standalone child entry point (not imported by the app):
1. Reads OHLCV from the input CSV.
2. Reads user code, `compile`s + `exec`s it against a restricted namespace: `__builtins__` limited to a small safe set (`len`, `range`, `min`, `max`, `sum`, `abs`, `round`, `sorted`, `enumerate`, `zip`, basic types/exceptions, plus a `__import__` shim that only permits `pandas`/`numpy`) with `pd`/`np` pre-bound — no `open`, `os`, `sys`, `globals`, `locals`, `input`.
3. Captures stdout/stderr during the call so stray `print()`s don't corrupt anything (output travels via the JSON file, not stdout).
4. Calls `generate_signals(df)`, validates it's a `pd.Series` of the right length with coercible `{-1,0,1}` values (after `fillna(0)`), writes the result (or a structured error) to `output.json`.

### Residual risk (documented, not engineered around)

Plain subprocess isolation has no container/seccomp/network-namespace boundary. Outbound network access is mitigated, not eliminated, by removing any reachable path to `socket`/`os`/`urllib` from the restricted builtins/import allowlist. This is judged acceptable for this project's personal/small-scale use (per CLAUDE.md's existing tone), not multi-tenant SaaS. Docker-per-run isolation was considered and explicitly deferred as overkill for this scale.

## Data model changes

None requiring migration. `Strategy.code` (`database/models.py`, `Text`, nullable) already exists and is currently dead — it becomes the landing spot for user source. A new optional `"mode"` key is added inside the existing `Strategy.parameters` JSON blob: `"mode": "custom_code"` vs. today's implicit default (`"rules"`, or simply an absent key) — fully backward compatible with every already-persisted strategy.

## Backend integration points

- `StrategyExecutor.__init__(strategy_config, code=None)` — new optional `code` param.
- `StrategyExecutor.validate()` — branches on `self.config.get('mode', 'rules')`; `custom_code` requires non-empty `code`; `rules` keeps today's exact required-field checks (`parameters`, `rules.entry`, `rules.exit`) unchanged.
- `StrategyExecutor.backtest()` — branches at the point where `_calculate_indicators` + the entry/exit loop run today: `custom_code` mode calls `sandbox_executor.run_custom_strategy` to build `df['signal']` instead; both branches feed the same unchanged `_execute_trades`/`_calculate_metrics`. `SandboxError` is re-raised as `ValueError`, which `backtest_service.py`'s existing `except Exception → HTTPException(400, str(e))` already surfaces cleanly — no new error handling needed there.
- `backtest_service.py` — one-line change: `StrategyExecutor(strategy.parameters, code=strategy.code)`.
- `routers/strategies.py` — `create_strategy` and `update_strategy` run `check_ast_safety` at save time (not just at backtest-run time) whenever the effective `mode` is `custom_code`, for fast feedback; `update_strategy`'s partial-dict `setattr` pattern is preserved, with validation applied against the merged (existing + incoming) view of `parameters`/`code`.

## Frontend changes

- `StrategyBuilder.jsx` — a "Visual Builder" / "Custom Python Code" toggle. Visual mode is unchanged. Custom-code mode shows a strategy-name field, a plain `<textarea>` (deliberately no CodeMirror/Monaco dependency for this first pass) pre-filled with a starter `generate_signals` template, and a static reference block listing available columns/names and the sandbox's resource limits. `handleSave` emits `{ name, mode: 'custom_code', code }` or the existing `{ name, mode: 'rules', parameters, rules }` shape.
- `StrategiesPage.jsx` — `handleSaveStrategy` shapes the POST payload's `parameters` JSON and top-level `code` field based on `mode`; the strategy list's summary line shows "Custom Python strategy" for `mode === 'custom_code'`; existing error display (`err?.response?.data?.detail`) already covers save-time and run-time sandbox errors with no new plumbing. Copy at the "without writing code" line is updated.
- `src/api/strategies.js` — no change needed.

## Testing

`BackEnd/tests/test_sandbox_executor.py` (new, real subprocess execution, no mocks — matches `test_strategy_executor.py`'s existing style): valid code produces expected signals; missing `generate_signals` rejected pre-spawn; disallowed import rejected; dunder-escape rejected; `eval`/`exec`/`open` calls rejected; allowed `numpy` usage works; timeout enforced (busy loop, short test-only limit); memory limit enforced (oversized allocation, small test-only limit); runtime exception surfaces with the original exception text; wrong-length output rejected; out-of-range values rejected; NaN coerced to 0.

`BackEnd/tests/test_strategy_executor.py` (extended): custom-code mode end-to-end through `backtest()` reusing `_execute_trades`/metrics; `validate()` rejects `custom_code` mode with no code; `validate()` still requires rules fields for default mode (no regression).

This feature touches no SQL date/type comparisons, so the Postgres-vs-SQLite dialect concern from CLAUDE.md doesn't apply — no `docker compose up` verification needed, only the standard `uv run pytest`.

## Explicitly out of scope (future work)

- Async job queue (Celery + Redis) for backtests generally, once real sandbox runtime cost is measured against the synchronous path.
- Richer code editor (CodeMirror/Monaco).
- Relaxing the strict `pd.Series`-only return requirement.
- Stop-loss/take-profit/position-sizing %, and buy-and-hold benchmark comparison — noted as the next roadmap items after this feature ships, not part of it.

import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

import services.sandbox_executor as sandbox_executor
from services.sandbox_executor import (
    check_ast_safety,
    run_custom_strategy,
    SandboxValidationError,
    SandboxTimeoutError,
    SandboxMemoryError,
    SandboxRuntimeError,
    SandboxOutputError,
)


def make_price_df(closes):
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    return pd.DataFrame({"close": closes}, index=dates)


def test_valid_code_produces_expected_signals():
    code = (
        "def generate_signals(df):\n"
        "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
        "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
        "    return up - down\n"
    )
    df = make_price_df([10, 12, 11, 13])
    result = run_custom_strategy(code, df)
    assert list(result) == [0, 1, -1, 1]


def test_missing_generate_signals_function_rejected():
    code = "x = 1\n"
    with pytest.raises(SandboxValidationError, match="generate_signals"):
        check_ast_safety(code)


def test_disallowed_import_rejected_by_ast_check():
    code = (
        "import os\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="os"):
        check_ast_safety(code)


def test_disallowed_import_violation_includes_line_number():
    code = (
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
        "import os\n"
    )
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations == [
        {"line": 3, "message": "import of 'os' is not allowed"},
    ]


def test_missing_generate_signals_violation_anchors_to_line_one():
    code = "x = 1\n"
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations == [
        {"line": 1, "message": "code must define a top-level function named 'generate_signals'"},
    ]


def test_syntax_error_violation_includes_line_number():
    code = "def generate_signals(df):\n    return df['close'] * 0\nif\n"
    with pytest.raises(SandboxValidationError) as exc_info:
        check_ast_safety(code)
    assert exc_info.value.violations[0]["line"] == 3
    assert "Syntax error" in exc_info.value.violations[0]["message"]


def test_validation_error_message_unchanged_for_existing_callers():
    code = (
        "import os\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="import of 'os' is not allowed"):
        check_ast_safety(code)


def test_dunder_attribute_access_rejected():
    code = (
        "def generate_signals(df):\n"
        "    x = ().__class__.__bases__\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError):
        check_ast_safety(code)


def test_deeply_nested_expression_rejected_cleanly():
    # Repeated unary `-` operators reliably blow Python's recursion limit
    # inside ast.NodeVisitor.generic_visit (verified directly against
    # _SafetyVisitor().visit(ast.parse(code)) before wrapping the fix around
    # it) without also tripping the parser's own stack limit the way deeply
    # nested parens do.
    code = "def generate_signals(df):\n    return " + "-" * 2000 + "1\n"
    with pytest.raises(SandboxValidationError, match="too deeply nested"):
        check_ast_safety(code)


@pytest.mark.parametrize("call", ["eval('1')", "exec('1')", "open('/etc/passwd')"])
def test_eval_exec_open_calls_rejected(call):
    code = (
        "def generate_signals(df):\n"
        f"    {call}\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError):
        check_ast_safety(code)


def test_allowed_pandas_numpy_usage_works():
    code = (
        "import numpy as np\n"
        "def generate_signals(df):\n"
        "    values = np.where(df['close'] > df['close'].mean(), 1, -1)\n"
        "    return pd.Series(values, index=df.index)\n"
    )
    df = make_price_df([10, 20, 5, 30])
    result = run_custom_strategy(code, df)
    assert set(result) <= {-1, 1}


def test_timeout_enforced():
    code = (
        "def generate_signals(df):\n"
        "    while True:\n"
        "        pass\n"
    )
    df = make_price_df([10, 11, 12])
    start = time.monotonic()
    with pytest.raises(SandboxTimeoutError):
        run_custom_strategy(code, df, timeout_s=2, cpu_limit_s=1)
    assert time.monotonic() - start < 8


def test_memory_limit_enforced():
    code = (
        "import numpy as np\n"
        "def generate_signals(df):\n"
        "    huge = np.zeros(300 * 1024 * 1024 // 8)\n"
        "    return df['close'] * 0\n"
    )
    df = make_price_df([10, 11, 12])
    with pytest.raises(SandboxMemoryError):
        run_custom_strategy(code, df, mem_limit_mb=300)


def test_runtime_exception_in_user_code_surfaces_cleanly():
    code = (
        "def generate_signals(df):\n"
        "    return 1 / 0\n"
    )
    df = make_price_df([10, 11, 12])
    with pytest.raises(SandboxRuntimeError, match="ZeroDivisionError"):
        run_custom_strategy(code, df)


def test_output_wrong_length_rejected():
    code = (
        "def generate_signals(df):\n"
        "    return df['close'].iloc[:1] * 0\n"
    )
    df = make_price_df([10, 11, 12])
    with pytest.raises(SandboxOutputError):
        run_custom_strategy(code, df)


def test_output_values_out_of_range_rejected():
    code = (
        "def generate_signals(df):\n"
        "    return df['close'] * 0 + 5\n"
    )
    df = make_price_df([10, 11, 12])
    with pytest.raises(SandboxOutputError):
        run_custom_strategy(code, df)


def test_nan_in_output_coerced_to_zero():
    code = (
        "def generate_signals(df):\n"
        "    s = df['close'].rolling(2).mean()\n"
        "    return (s > 0).astype(int).where(s.notna())\n"
    )
    df = make_price_df([10, 11, 12])
    result = run_custom_strategy(code, df)
    assert result.iloc[0] == 0


def test_curated_sklearn_submodule_import_allowed():
    code = (
        "from sklearn.linear_model import LogisticRegression\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    check_ast_safety(code)  # does not raise


def test_sklearn_datasets_submodule_rejected():
    code = (
        "from sklearn.datasets import fetch_openml\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="sklearn.datasets"):
        check_ast_safety(code)


def test_bare_sklearn_import_rejected():
    code = (
        "import sklearn\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="sklearn"):
        check_ast_safety(code)


def test_from_sklearn_import_submodule_shorthand_rejected():
    # `from sklearn import linear_model` names the module as bare "sklearn",
    # not "sklearn.linear_model" - the curated allowlist only recognizes the
    # fully-qualified submodule form (`from sklearn.linear_model import ...`).
    code = (
        "from sklearn import linear_model\n"
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    with pytest.raises(SandboxValidationError, match="sklearn"):
        check_ast_safety(code)


def test_sklearn_model_trains_and_predicts_end_to_end():
    code = (
        "from sklearn.linear_model import LogisticRegression\n"
        "def generate_signals(df):\n"
        "    X = df[['close']].values\n"
        "    y = (df['close'] > df['close'].mean()).astype(int).values\n"
        "    model = LogisticRegression(n_jobs=5).fit(X, y)\n"
        "    preds = model.predict(X)\n"
        "    return pd.Series(preds, index=df.index)\n"
    )
    df = make_price_df([10, 20, 5, 30, 15, 25, 8, 18])
    result = run_custom_strategy(code, df)
    assert set(result) <= {-1, 0, 1}
    assert len(result) == len(df)


def test_sklearn_forced_single_threaded_env_vars_set(monkeypatch):
    captured = {}
    real_run = sandbox_executor.subprocess.run

    def spy_run(*args, **kwargs):
        captured["env"] = kwargs.get("env")
        return real_run(*args, **kwargs)

    monkeypatch.setattr(sandbox_executor.subprocess, "run", spy_run)
    code = (
        "def generate_signals(df):\n"
        "    return df['close'] * 0\n"
    )
    df = make_price_df([10, 11, 12])
    run_custom_strategy(code, df)
    env = captured["env"]
    assert env is not None
    assert env["OMP_NUM_THREADS"] == "1"
    assert env["OPENBLAS_NUM_THREADS"] == "1"
    assert env["MKL_NUM_THREADS"] == "1"
    assert env["JOBLIB_MULTIPROCESSING"] == "0"
    assert env["LOKY_MAX_CPU_COUNT"] == "1"


def test_resource_limit_defaults_read_from_env():
    # A fresh subprocess, not importlib.reload() in-process: reloading
    # sandbox_executor here would replace its exception classes/functions
    # with new objects, desyncing `except SandboxValidationError` in
    # routers/strategies.py (imported by reference elsewhere in the suite)
    # from what check_ast_safety actually raises after the reload.
    backend_dir = Path(__file__).resolve().parent.parent
    env = {
        **os.environ,
        "SANDBOX_TIMEOUT_S": "42",
        "SANDBOX_MEM_LIMIT_MB": "999",
        "SANDBOX_CPU_LIMIT_S": "17",
    }
    result = subprocess.run(
        [sys.executable, "-c",
         "import services.sandbox_executor as m; "
         "print(m.DEFAULT_TIMEOUT_S, m.DEFAULT_MEM_LIMIT_MB, m.DEFAULT_CPU_LIMIT_S)"],
        cwd=backend_dir, env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.stdout.strip() == "42 999 17", result.stderr


def test_resource_limit_defaults_fall_back_when_env_unset():
    assert sandbox_executor.DEFAULT_TIMEOUT_S == 10
    assert sandbox_executor.DEFAULT_MEM_LIMIT_MB == 512
    assert sandbox_executor.DEFAULT_CPU_LIMIT_S == 8

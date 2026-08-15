"""
Sandbox child process entry point.

Not imported by the app - invoked as a standalone script by
services.sandbox_executor.run_custom_strategy(), already running under the
resource limits (RLIMIT_AS/RLIMIT_CPU) the parent set via preexec_fn before
this process's Python interpreter (and its pandas/numpy import) even starts.

Executes the user's `generate_signals(df)` against a restricted builtins
namespace and writes the result (or a structured error) to a JSON file -
never to stdout, which is captured during the call so stray print()s in user
code can't corrupt the output channel.
"""
import io
import json
import sys


# Mirrors services.sandbox_executor.ALLOWED_IMPORT_PREFIXES - kept as a
# literal copy rather than an import so this worker has no dependency on
# the parent package at runtime (it's invoked as a standalone script).
_ALLOWED_IMPORT_PREFIXES = (
    "pandas", "numpy",
    "sklearn.linear_model", "sklearn.ensemble", "sklearn.tree",
    "sklearn.svm", "sklearn.naive_bayes", "sklearn.neighbors",
    "sklearn.preprocessing", "sklearn.pipeline",
    "sklearn.model_selection", "sklearn.metrics",
)


def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    allowed = any(
        name == prefix or name.startswith(prefix + ".")
        for prefix in _ALLOWED_IMPORT_PREFIXES
    )
    if not allowed:
        raise ImportError(f"import of {name!r} is not allowed in custom strategy code")
    return __import__(name, globals, locals, fromlist, level)


SAFE_BUILTINS = {
    "len": len, "range": range, "min": min, "max": max, "sum": sum, "abs": abs,
    "round": round, "sorted": sorted, "enumerate": enumerate, "zip": zip,
    "list": list, "dict": dict, "set": set, "tuple": tuple, "float": float,
    "int": int, "str": str, "bool": bool, "True": True, "False": False, "None": None,
    "isinstance": isinstance, "Exception": Exception, "ValueError": ValueError,
    "TypeError": TypeError, "ZeroDivisionError": ZeroDivisionError,
    "KeyError": KeyError, "IndexError": IndexError, "ArithmeticError": ArithmeticError,
    "__import__": _safe_import,
}


def _write_result(output_path, payload):
    with open(output_path, "w") as f:
        json.dump(payload, f)


def main(code_path, input_csv_path, output_json_path):
    import pandas as pd

    try:
        with open(code_path) as f:
            source = f.read()
        df = pd.read_csv(input_csv_path, parse_dates=["date"], index_col="date")

        exec_globals = {"__builtins__": SAFE_BUILTINS, "pd": pd}
        try:
            import numpy as np
            exec_globals["np"] = np
        except ImportError:
            pass

        compiled = compile(source, "<custom_strategy>", "exec")
        exec(compiled, exec_globals)

        generate_signals = exec_globals.get("generate_signals")
        if not callable(generate_signals):
            _write_result(output_json_path, {
                "error_type": "ValidationError",
                "message": "generate_signals is not defined",
            })
            return 1

        captured = io.StringIO()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = captured
        try:
            result = generate_signals(df)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        if not isinstance(result, pd.Series):
            _write_result(output_json_path, {
                "error_type": "OutputError",
                "message": f"generate_signals must return a pandas Series, got {type(result).__name__}",
            })
            return 1

        if len(result) != len(df):
            _write_result(output_json_path, {
                "error_type": "OutputError",
                "message": f"generate_signals returned {len(result)} values, expected {len(df)}",
            })
            return 1

        result = result.reindex(df.index).fillna(0)

        signals = []
        bad_values = []
        for v in result.tolist():
            try:
                iv = int(round(float(v)))
            except (TypeError, ValueError):
                bad_values.append(v)
                continue
            if iv not in (-1, 0, 1):
                bad_values.append(v)
                continue
            signals.append(iv)

        if bad_values:
            _write_result(output_json_path, {
                "error_type": "OutputError",
                "message": f"generate_signals returned values outside {{-1, 0, 1}}: {bad_values[:5]}",
            })
            return 1

        _write_result(output_json_path, signals)
        return 0
    except MemoryError:
        _write_result(output_json_path, {
            "error_type": "MemoryError",
            "message": "Custom strategy code exceeded the memory limit",
        })
        return 1
    except Exception as e:
        _write_result(output_json_path, {
            "error_type": type(e).__name__,
            "message": str(e),
        })
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))

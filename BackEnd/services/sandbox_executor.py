"""
Sandboxed execution of user-submitted "custom code" strategies.

A custom-code strategy's only responsibility is producing a per-bar signal
series (see check_ast_safety's required `generate_signals(df)` entry point);
position sizing, commission/slippage, and metrics stay in
StrategyExecutor._execute_trades/_calculate_metrics, unchanged.

Two layers of defense, matching the whitelist-not-eval philosophy already
used by StrategyExecutor._evaluate_condition rather than pulling in a new
dependency like RestrictedPython:
  1. check_ast_safety() statically rejects disallowed imports/calls/dunder
     access before anything is spawned.
  2. run_custom_strategy() then executes the code in a separate subprocess
     (services/_sandbox_worker.py) with OS-enforced memory/CPU limits and a
     wall-clock timeout, and a restricted builtins namespace as defense in
     depth.

Residual risk (accepted, not engineered around): plain subprocess isolation
has no container/seccomp/network-namespace boundary, so it does not block
outbound network access at the OS level. This is mitigated, not eliminated,
by the restricted builtins/import allowlist removing any reachable path to
socket/os/urllib from user code. Judged acceptable for this project's
personal/small-scale use, not multi-tenant SaaS.
"""
import ast
import json
import os
import resource
import signal
import subprocess
import sys
import tempfile

import pandas as pd

_WORKER_PATH = os.path.join(os.path.dirname(__file__), "_sandbox_worker.py")

DEFAULT_TIMEOUT_S = 10
DEFAULT_MEM_LIMIT_MB = 512
DEFAULT_CPU_LIMIT_S = 8

ALLOWED_IMPORT_ROOTS = {"pandas", "numpy"}
DISALLOWED_CALL_NAMES = {
    "eval", "exec", "compile", "__import__", "open", "input",
    "globals", "locals", "vars", "exit", "quit",
}


class SandboxError(Exception):
    """Base class for all custom-strategy sandbox errors."""


class SandboxValidationError(SandboxError):
    """Code failed the pre-execution safety check (syntax or disallowed construct).

    `.violations` is a list of {"line": int | None, "message": str} dicts for
    structured, line-anchored consumers (e.g. editor diagnostics). Falls back
    to a single {"line": None, "message": str(self)} entry when constructed
    without one, so callers that just want str(exc) are unaffected.
    """

    def __init__(self, message, violations=None):
        super().__init__(message)
        self.violations = violations if violations is not None else [{"line": None, "message": message}]


class SandboxTimeoutError(SandboxError):
    """Code exceeded the wall-clock execution limit."""


class SandboxMemoryError(SandboxError):
    """Code exceeded the memory limit and was terminated."""


class SandboxRuntimeError(SandboxError):
    """Code raised an exception while generating signals."""


class SandboxOutputError(SandboxError):
    """Code returned a value that doesn't match the required signal contract."""


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self):
        self.violations = []  # list of {"line": int, "message": str}

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                self.violations.append({
                    "line": node.lineno,
                    "message": f"import of {alias.name!r} is not allowed",
                })
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        root = (node.module or "").split(".")[0]
        if root not in ALLOWED_IMPORT_ROOTS:
            self.violations.append({
                "line": node.lineno,
                "message": f"import of {node.module!r} is not allowed",
            })
        self.generic_visit(node)

    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in DISALLOWED_CALL_NAMES:
            self.violations.append({
                "line": node.lineno,
                "message": f"call to {node.func.id}() is not allowed",
            })
        self.generic_visit(node)

    def visit_Name(self, node):
        if node.id.startswith("__") and node.id.endswith("__"):
            self.violations.append({
                "line": node.lineno,
                "message": f"reference to {node.id!r} is not allowed",
            })
        self.generic_visit(node)

    def visit_Attribute(self, node):
        if node.attr.startswith("__") and node.attr.endswith("__"):
            self.violations.append({
                "line": node.lineno,
                "message": f"attribute access to {node.attr!r} is not allowed",
            })
        self.generic_visit(node)


def check_ast_safety(code: str) -> None:
    """Raise SandboxValidationError if `code` doesn't meet the custom-code
    strategy contract: a top-level `generate_signals` function, imports
    limited to pandas/numpy, no eval/exec/open/etc., no dunder access."""
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise SandboxValidationError(
            f"Syntax error: {e}",
            violations=[{"line": e.lineno, "message": f"Syntax error: {e.msg}"}],
        ) from e

    has_entrypoint = any(
        isinstance(node, ast.FunctionDef) and node.name == "generate_signals"
        for node in tree.body
    )

    visitor = _SafetyVisitor()
    try:
        visitor.visit(tree)
    except RecursionError as e:
        raise SandboxValidationError(
            "code is too deeply nested to validate",
            violations=[{"line": 1, "message": "code is too deeply nested to validate"}],
        ) from e

    violations = list(visitor.violations)
    if not has_entrypoint:
        violations.insert(0, {
            "line": 1,
            "message": "code must define a top-level function named 'generate_signals'",
        })

    if violations:
        raise SandboxValidationError(
            "; ".join(v["message"] for v in violations),
            violations=violations,
        )


def _limit_resources(mem_limit_mb: int, cpu_limit_s: int):
    def _set():
        mem_bytes = mem_limit_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        # Soft limit < hard limit so the kernel delivers a catchable SIGXCPU
        # at cpu_limit_s (distinguishable from an OOM SIGKILL below) before
        # falling back to SIGKILL a second later if the process is still
        # running.
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit_s, cpu_limit_s + 1))
    return _set


def run_custom_strategy(
    code: str,
    df: pd.DataFrame,
    *,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    mem_limit_mb: int = DEFAULT_MEM_LIMIT_MB,
    cpu_limit_s: int = DEFAULT_CPU_LIMIT_S,
) -> pd.Series:
    """Run user `code`'s generate_signals(df) in a resource-limited
    subprocess and return the resulting per-bar signal Series."""
    check_ast_safety(code)

    with tempfile.TemporaryDirectory() as tmp_dir:
        code_path = os.path.join(tmp_dir, "strategy.py")
        input_csv_path = os.path.join(tmp_dir, "input.csv")
        output_json_path = os.path.join(tmp_dir, "output.json")

        with open(code_path, "w") as f:
            f.write(code)
        os.chmod(code_path, 0o600)

        out_df = df.reset_index()
        out_df = out_df.rename(columns={out_df.columns[0]: "date"})
        out_df.to_csv(input_csv_path, index=False)
        os.chmod(input_csv_path, 0o600)

        try:
            proc = subprocess.run(
                [sys.executable, _WORKER_PATH, code_path, input_csv_path, output_json_path],
                timeout=timeout_s,
                capture_output=True,
                text=True,
                preexec_fn=_limit_resources(mem_limit_mb, cpu_limit_s),
            )
        except subprocess.TimeoutExpired:
            raise SandboxTimeoutError(f"Custom strategy code timed out after {timeout_s}s")

        payload = None
        if os.path.exists(output_json_path):
            try:
                with open(output_json_path) as f:
                    payload = json.load(f)
            except (json.JSONDecodeError, OSError):
                payload = None

        if isinstance(payload, list):
            return pd.Series(payload, index=df.index, dtype=int)

        if isinstance(payload, dict) and "error_type" in payload:
            error_type = payload["error_type"]
            message = payload.get("message", "Custom strategy code failed")
            if error_type == "ValidationError":
                raise SandboxValidationError(message)
            if error_type == "OutputError":
                raise SandboxOutputError(message)
            if error_type == "MemoryError":
                raise SandboxMemoryError(message)
            raise SandboxRuntimeError(f"{error_type}: {message}")

        if proc.returncode < 0:
            sig = -proc.returncode
            if sig == signal.SIGXCPU:
                raise SandboxTimeoutError(
                    f"Custom strategy code exceeded the CPU time limit of {cpu_limit_s}s"
                )
            raise SandboxMemoryError(
                "Custom strategy code was terminated (likely exceeded the memory limit)"
            )

        stderr_tail = (proc.stderr or "").strip()[-500:]
        raise SandboxError(
            "Custom strategy code failed for an unknown reason"
            + (f": {stderr_tail}" if stderr_tail else "")
        )

"""Walk-forward (expanding-window out-of-sample) evaluation for custom-code
(ML) strategies. Instead of fitting/predicting once over an entire date
range (which lets a model implicitly "see" the whole history before being
scored on any of it), this splits the range into expanding train/test
folds, re-runs StrategyExecutor.generate_signals() fresh each fold, and
stitches the out-of-sample results into one continuous equity curve with
capital compounding across folds.

Scoped to custom_code strategies only — see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md.
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List


def compute_fold_boundaries(
    start_dt: datetime, end_dt: datetime, test_window_days: int
) -> List[Dict[str, Any]]:
    """Expanding-window fold boundaries. The first fold's train window is
    max(365 days, 25% of the total range), anchored at start_dt; each
    fold's test window is test_window_days long (inclusive), stepping
    forward from the end of the previous fold's test window. A trailing
    remainder shorter than test_window_days is dropped rather than becoming
    a short partial fold.

    Returns a list of {fold_index, train_start, train_end, test_start,
    test_end} dicts (train_end/test_end inclusive).

    Raises:
        ValueError: the range doesn't fit even one full fold.
    """
    total_days = (end_dt - start_dt).days
    initial_train_days = max(365, int(total_days * 0.25))

    folds = []
    fold_index = 0
    test_start = start_dt + timedelta(days=initial_train_days)
    while True:
        test_end = test_start + timedelta(days=test_window_days - 1)
        if test_end > end_dt:
            break
        folds.append({
            "fold_index": fold_index,
            "train_start": start_dt,
            "train_end": test_start - timedelta(days=1),
            "test_start": test_start,
            "test_end": test_end,
        })
        fold_index += 1
        test_start = test_end + timedelta(days=1)

    if not folds:
        raise ValueError("date range too short for the requested test window")
    return folds


def estimate_fold_count(start_dt: datetime, end_dt: datetime, test_window_days: int) -> int:
    """Conservative (over-)estimate of fold count, used to size the Celery
    task's time limit before folds are actually computed (that requires the
    market data itself, which isn't loaded until the task runs). Deliberately
    ignores the initial-train-window subtraction that compute_fold_boundaries
    applies, so this always estimates at or above the real fold count —
    erring toward a longer, safer time limit rather than a tight one that
    could clip a real run."""
    total_days = (end_dt - start_dt).days
    return max(1, -(-total_days // test_window_days))  # ceiling division

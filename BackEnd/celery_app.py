"""Celery application for async execution of long-running backtests and
market-data imports. Redis is used as the broker only — task status and
results are persisted directly on the BacktestResult/PortfolioBacktestResult/
DataImportJob rows in Postgres (see docs/superpowers/specs/
2026-08-14-celery-redis-async-tasks-design.md), not in a Celery result
backend, so there's nothing to configure there."""
import os

from celery import Celery

celery_app = Celery(
    "quanttrade",
    broker=os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0"),
    backend=None,
)
celery_app.conf.update(
    task_track_started=True,
    # 11 min hard kill / 10 min soft — soft is catchable inside the task
    # (mirrors the RLIMIT_CPU soft-before-hard pattern in
    # services/sandbox_executor.py), hard is the SIGKILL backstop.
    task_time_limit=660,
    task_soft_time_limit=600,
    worker_prefetch_multiplier=1,
    # Lets tests run task.delay() synchronously in-process with no real
    # broker — see tests/conftest.py (added in Task 6).
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true",
)

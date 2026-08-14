"""Celery task definitions. Each task is a thin sync wrapper — the actual
work is an existing async service function, run via asyncio.run() inside a
fresh event loop the task owns for its whole lifetime. Started as:

    celery -A tasks.celery_app worker --loglevel=info --concurrency=2

(pointing -A directly at this module, not celery_app.py, so importing it
for app discovery also registers every @celery_app.task below — see
docs/superpowers/specs/2026-08-14-celery-redis-async-tasks-design.md)."""
import asyncio
import base64
from typing import Optional

import httpx
from celery.exceptions import MaxRetriesExceededError

from celery_app import celery_app
from worker_db import get_worker_session_factory


async def _run_with_session(fn, *args) -> None:
    session_factory = get_worker_session_factory()
    async with session_factory() as db:
        await fn(*args, db)


@celery_app.task(name="tasks.run_backtest_task")
def run_backtest_task(backtest_result_id: int) -> None:
    from services.backtest_service import execute_backtest
    asyncio.run(_run_with_session(execute_backtest, backtest_result_id))


@celery_app.task(name="tasks.run_portfolio_backtest_task")
def run_portfolio_backtest_task(portfolio_backtest_id: int) -> None:
    from services.portfolio_backtest_service import execute_portfolio_backtest
    asyncio.run(_run_with_session(execute_portfolio_backtest, portfolio_backtest_id))


@celery_app.task(name="tasks.upload_csv_task")
def upload_csv_task(job_id: int, ticker: Optional[str], content_b64: str) -> None:
    from services.data_import_service import execute_csv_import
    content = base64.b64decode(content_b64)
    asyncio.run(_run_with_session(execute_csv_import, job_id, ticker, content))


@celery_app.task(bind=True, name="tasks.import_alpha_vantage_task", max_retries=2, default_retry_delay=5)
def import_alpha_vantage_task(
    self, job_id: int, ticker: str, api_key: str, outputsize: str,
    start_date: Optional[str], end_date: Optional[str],
) -> None:
    from services.data_import_service import _mark_job_failed, execute_alpha_vantage_import
    try:
        asyncio.run(_run_with_session(
            execute_alpha_vantage_import, job_id, ticker, api_key, outputsize, start_date, end_date
        ))
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        try:
            raise self.retry(exc=exc)
        except MaxRetriesExceededError:
            asyncio.run(_run_with_session(
                _mark_job_failed, job_id, f"Could not reach data provider after retries: {exc}"
            ))

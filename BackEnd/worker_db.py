"""Lazy, post-fork-safe async DB engine for Celery worker processes.

database/connection.py's engine is created once at import time — fine for
the API process, which never forks after import, but unsafe to reuse
directly in a Celery prefork worker: asyncpg connections do not survive
fork(). This module instead builds its own AsyncEngine/sessionmaker on
first use inside each forked worker child, never at import time.

Uses NullPool rather than a pooled engine: each Celery task body runs
inside its own asyncio.run() call (see tasks.py), which spins up a brand
new event loop per task. asyncpg connections are bound to the event loop
they were opened on, so a pooled connection checked out on task N's loop
and reused on task N+1's (different) loop raises `RuntimeError: Task ...
got Future ... attached to a different loop` — reproduced against a real
multi-task Celery worker run against Postgres (SQLite/aiosqlite tolerates
this, so the dialect-sensitive eager-mode test suite never caught it, same
class of gap as the CLAUDE.md-documented raw-string-date bug). NullPool
opens a fresh connection per checkout and closes it on checkin, so no
connection object is ever reused across event loops."""
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

_engine = None
_session_factory = None


def get_worker_session_factory() -> async_sessionmaker:
    global _engine, _session_factory
    if _session_factory is None:
        database_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"
        )
        _engine = create_async_engine(database_url, poolclass=NullPool)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _session_factory

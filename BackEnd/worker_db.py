"""Lazy, post-fork-safe async DB engine for Celery worker processes.

database/connection.py's engine is created once at import time — fine for
the API process, which never forks after import, but unsafe to reuse
directly in a Celery prefork worker: asyncpg connections do not survive
fork(). This module instead builds its own AsyncEngine/sessionmaker on
first use inside each forked worker child, never at import time."""
import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

_engine = None
_session_factory = None


def get_worker_session_factory() -> async_sessionmaker:
    global _engine, _session_factory
    if _session_factory is None:
        database_url = os.getenv(
            "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"
        )
        _engine = create_async_engine(database_url, pool_pre_ping=True, pool_size=2)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _session_factory

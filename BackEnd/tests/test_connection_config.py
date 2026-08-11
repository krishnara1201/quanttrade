import importlib
import os

import database.connection as connection


def test_database_url_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"


def test_database_url_reads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@postgres:5432/quanttrade")
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://user:pass@postgres:5432/quanttrade"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    importlib.reload(connection)

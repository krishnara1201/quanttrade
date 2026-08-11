import importlib

import dotenv

import database.connection as connection


def test_database_url_falls_back_to_hardcoded_default(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://postgres:postgres@localhost/quanttrade"
    # Undo the monkeypatches (restores real load_dotenv and any real
    # DATABASE_URL env var) before reloading again, so later test modules
    # that import database.connection see normal, unpatched behavior.
    monkeypatch.undo()
    importlib.reload(connection)


def test_database_url_reads_from_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://user:pass@postgres:5432/quanttrade")
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    importlib.reload(connection)
    assert connection.DATABASE_URL == "postgresql+asyncpg://user:pass@postgres:5432/quanttrade"
    monkeypatch.undo()
    importlib.reload(connection)

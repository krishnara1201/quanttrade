import importlib

import dotenv
import pytest

import services.auth_service as auth_service


def test_config_defaults(monkeypatch):
    for var in ("ACCESS_TOKEN_EXPIRE_MINUTES", "REFRESH_TOKEN_EXPIRE_DAYS", "COOKIE_SECURE", "LOGIN_MAX_ATTEMPTS", "LOGIN_LOCKOUT_MINUTES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: None)
    importlib.reload(auth_service)

    assert auth_service.ACCESS_TOKEN_EXPIRE_MINUTES == 15
    assert auth_service.REFRESH_TOKEN_EXPIRE_DAYS == 7
    assert auth_service.COOKIE_SECURE is True
    assert auth_service.LOGIN_MAX_ATTEMPTS == 5
    assert auth_service.LOGIN_LOCKOUT_MINUTES == 15

    monkeypatch.undo()
    importlib.reload(auth_service)


def test_validate_secret_key_raises_when_unset(monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", None)
    with pytest.raises(RuntimeError):
        auth_service.validate_secret_key()


def test_validate_secret_key_raises_when_still_the_placeholder(monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", auth_service.SECRET_KEY_PLACEHOLDER)
    with pytest.raises(RuntimeError):
        auth_service.validate_secret_key()


def test_validate_secret_key_passes_for_a_real_secret(monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", "a-real-random-secret-value")
    auth_service.validate_secret_key()  # must not raise

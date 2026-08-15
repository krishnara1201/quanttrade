import sys

import pytest

import services.auth_service as auth_service


def test_app_fails_to_start_with_an_invalid_secret_key(monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", None)
    sys.modules.pop("app", None)
    with pytest.raises(RuntimeError):
        import app  # noqa: F401
    sys.modules.pop("app", None)


def test_app_starts_with_a_valid_secret_key(monkeypatch):
    monkeypatch.setattr(auth_service, "SECRET_KEY", "a-real-random-secret-value")
    sys.modules.pop("app", None)
    import app  # noqa: F401
    sys.modules.pop("app", None)

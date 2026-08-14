import importlib
import os


def test_celery_app_reads_broker_url_from_env(monkeypatch):
    monkeypatch.setenv("CELERY_BROKER_URL", "redis://example-host:6379/2")
    import celery_app as celery_app_module
    importlib.reload(celery_app_module)
    assert celery_app_module.celery_app.conf.broker_url == "redis://example-host:6379/2"
    assert celery_app_module.celery_app.conf.result_backend is None


def test_celery_app_defaults_to_localhost_broker(monkeypatch):
    monkeypatch.delenv("CELERY_BROKER_URL", raising=False)
    import celery_app as celery_app_module
    importlib.reload(celery_app_module)
    assert celery_app_module.celery_app.conf.broker_url == "redis://localhost:6379/0"


def test_celery_app_task_always_eager_toggles_from_env(monkeypatch):
    monkeypatch.setenv("CELERY_TASK_ALWAYS_EAGER", "true")
    import celery_app as celery_app_module
    importlib.reload(celery_app_module)
    assert celery_app_module.celery_app.conf.task_always_eager is True

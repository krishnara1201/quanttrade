"""Session-wide test config. task_always_eager makes every Celery
`.delay(...)` call in the test suite execute synchronously in-process
instead of publishing to a real broker — no Redis needed to run tests,
consistent with this repo's no-mocks/real-execution testing style."""
import pytest

from celery_app import celery_app


@pytest.fixture(autouse=True, scope="session")
def _celery_eager_mode():
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield

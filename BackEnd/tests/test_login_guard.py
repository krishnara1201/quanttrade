from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User
from services import login_guard


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def user_id(session_factory):
    async with session_factory() as db:
        u = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(u)
        await db.commit()
        await db.refresh(u)
        return u.id


def test_is_locked_false_when_locked_until_is_none():
    u = User(name="A", email="a@a.com", password_hash="x", locked_until=None)
    assert login_guard.is_locked(u) is False


def test_is_locked_false_when_locked_until_is_in_the_past():
    u = User(name="A", email="a@a.com", password_hash="x", locked_until=datetime.utcnow() - timedelta(minutes=1))
    assert login_guard.is_locked(u) is False


def test_is_locked_true_when_locked_until_is_in_the_future():
    u = User(name="A", email="a@a.com", password_hash="x", locked_until=datetime.utcnow() + timedelta(minutes=1))
    assert login_guard.is_locked(u) is True


@pytest.mark.asyncio
async def test_register_failed_attempt_increments_counter_below_threshold(session_factory, user_id):
    async with session_factory() as db:
        db_user = await db.get(User, user_id)
        await login_guard.register_failed_attempt(db, db_user, max_attempts=5, lockout_minutes=15)

    async with session_factory() as db:
        db_user = await db.get(User, user_id)
    assert db_user.failed_login_attempts == 1
    assert db_user.locked_until is None


@pytest.mark.asyncio
async def test_register_failed_attempt_locks_account_at_threshold_and_resets_counter(session_factory, user_id):
    async with session_factory() as db:
        db_user = await db.get(User, user_id)
        for _ in range(5):
            await login_guard.register_failed_attempt(db, db_user, max_attempts=5, lockout_minutes=15)

    async with session_factory() as db:
        db_user = await db.get(User, user_id)
    assert db_user.failed_login_attempts == 0
    assert db_user.locked_until is not None
    assert db_user.locked_until > datetime.utcnow()


@pytest.mark.asyncio
async def test_reset_failed_attempts_clears_counter_and_lock(session_factory, user_id):
    async with session_factory() as db:
        db_user = await db.get(User, user_id)
        db_user.failed_login_attempts = 3
        db_user.locked_until = datetime.utcnow() + timedelta(minutes=10)
        await db.commit()

    async with session_factory() as db:
        db_user = await db.get(User, user_id)
        await login_guard.reset_failed_attempts(db, db_user)

    async with session_factory() as db:
        db_user = await db.get(User, user_id)
    assert db_user.failed_login_attempts == 0
    assert db_user.locked_until is None

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, RefreshToken


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_user_defaults_no_failed_attempts_and_not_locked(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)

    assert user.failed_login_attempts == 0
    assert user.locked_until is None


@pytest.mark.asyncio
async def test_refresh_token_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        token = RefreshToken(
            user_id=user.id,
            token_hash="a" * 64,
            expires_at=datetime.utcnow() + timedelta(days=7),
        )
        db.add(token)
        await db.commit()
        await db.refresh(token)

    assert token.revoked_at is None
    assert token.created_at is not None
    assert token.user_id == user.id


@pytest.mark.asyncio
async def test_refresh_token_hash_must_be_unique(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()

        db.add(RefreshToken(user_id=user.id, token_hash="dup", expires_at=datetime.utcnow() + timedelta(days=7)))
        await db.commit()

        db.add(RefreshToken(user_id=user.id, token_hash="dup", expires_at=datetime.utcnow() + timedelta(days=7)))
        with pytest.raises(IntegrityError):
            await db.commit()

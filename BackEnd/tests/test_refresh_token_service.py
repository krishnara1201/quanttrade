from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, RefreshToken
from services import refresh_token_service as svc


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
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user.id


@pytest.mark.asyncio
async def test_create_refresh_token_persists_only_the_hash(session_factory, user_id):
    async with session_factory() as db:
        raw_token, record = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()

    assert record.token_hash != raw_token
    assert len(record.token_hash) == 64  # sha256 hex digest
    assert record.revoked_at is None


@pytest.mark.asyncio
async def test_rotate_refresh_token_issues_a_new_token_and_revokes_the_old_one(session_factory, user_id):
    async with session_factory() as db:
        raw_token, old_record = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()
        old_id = old_record.id

    async with session_factory() as db:
        new_raw_token, new_record = await svc.rotate_refresh_token(db, raw_token, expire_days=7)

    assert new_raw_token != raw_token
    assert new_record.user_id == user_id

    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == old_id))
        old = result.scalars().first()
    assert old.revoked_at is not None


@pytest.mark.asyncio
async def test_rotate_unknown_token_raises_value_error(session_factory):
    async with session_factory() as db:
        with pytest.raises(ValueError):
            await svc.rotate_refresh_token(db, "not-a-real-token", expire_days=7)


@pytest.mark.asyncio
async def test_rotate_expired_token_raises_value_error(session_factory, user_id):
    async with session_factory() as db:
        raw_token, record = await svc.create_refresh_token(db, user_id, expire_days=7)
        record.expires_at = datetime.utcnow() - timedelta(days=1)
        await db.commit()

    async with session_factory() as db:
        with pytest.raises(ValueError):
            await svc.rotate_refresh_token(db, raw_token, expire_days=7)


@pytest.mark.asyncio
async def test_replaying_an_already_rotated_token_revokes_every_live_token_for_that_user(session_factory, user_id):
    async with session_factory() as db:
        raw_token, _ = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()

    async with session_factory() as db:
        _, new_record = await svc.rotate_refresh_token(db, raw_token, expire_days=7)
        new_id = new_record.id

    async with session_factory() as db:
        with pytest.raises(svc.RefreshTokenReuseDetected):
            await svc.rotate_refresh_token(db, raw_token, expire_days=7)

    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == new_id))
        still_valid_looking = result.scalars().first()
    assert still_valid_looking.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_refresh_token_marks_it_revoked(session_factory, user_id):
    async with session_factory() as db:
        raw_token, record = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()
        token_id = record.id

    async with session_factory() as db:
        await svc.revoke_refresh_token(db, raw_token)

    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == token_id))
        record = result.scalars().first()
    assert record.revoked_at is not None


@pytest.mark.asyncio
async def test_revoke_refresh_token_is_a_no_op_for_an_unknown_token(session_factory):
    async with session_factory() as db:
        await svc.revoke_refresh_token(db, "does-not-exist")  # must not raise

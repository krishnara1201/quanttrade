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
        raw_token, old_record = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()
        old_id = old_record.id

    async with session_factory() as db:
        _, new_record = await svc.rotate_refresh_token(db, raw_token, expire_days=7)
        new_id = new_record.id

    # Backdate the rotated-away record's revoked_at to outside the grace
    # window so this test deterministically exercises the "old replay"
    # path (a realistic stolen/replayed token) rather than depending on
    # real wall-clock timing between the two rotate_refresh_token calls
    # above, which would otherwise still be within the grace window.
    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == old_id))
        old = result.scalars().first()
        old.revoked_at = datetime.utcnow() - timedelta(seconds=svc.REUSE_GRACE_WINDOW_SECONDS + 30)
        await db.commit()

    async with session_factory() as db:
        with pytest.raises(svc.RefreshTokenReuseDetected):
            await svc.rotate_refresh_token(db, raw_token, expire_days=7)

    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == new_id))
        still_valid_looking = result.scalars().first()
    assert still_valid_looking.revoked_at is not None


@pytest.mark.asyncio
async def test_replaying_a_token_rotated_within_the_grace_window_raises_plain_value_error_without_revoking_others(session_factory, user_id):
    # Simulates a benign cross-tab race: Tab A rotates T0 -> T1 (success).
    # Tab B then presents T0 again a moment later, before its cookie jar
    # picked up T1. This must be rejected (the stale request shouldn't
    # succeed) but must NOT nuke T1 or any other live session for the user.
    async with session_factory() as db:
        raw_token, _ = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()

    # A second, independent live token for the same user (e.g. another
    # device/session) that must survive the benign race untouched.
    async with session_factory() as db:
        other_raw_token, other_record = await svc.create_refresh_token(db, user_id, expire_days=7)
        await db.commit()
        other_id = other_record.id

    async with session_factory() as db:
        new_raw_token, _ = await svc.rotate_refresh_token(db, raw_token, expire_days=7)

    # Immediate replay of the now-rotated-away raw_token — well within the
    # 30s grace window given how fast this test runs.
    async with session_factory() as db:
        with pytest.raises(ValueError) as exc_info:
            await svc.rotate_refresh_token(db, raw_token, expire_days=7)
    assert not isinstance(exc_info.value, svc.RefreshTokenReuseDetected)

    # The legitimate successor token from Tab A's rotation must still work.
    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == svc._hash_token(new_raw_token)))
        successor = result.scalars().first()
    assert successor.revoked_at is None

    # The unrelated, separately-created live token for this user must also
    # still be untouched.
    async with session_factory() as db:
        result = await db.execute(select(RefreshToken).where(RefreshToken.id == other_id))
        other = result.scalars().first()
    assert other.revoked_at is None

    # And it's genuinely still usable.
    async with session_factory() as db:
        await svc.rotate_refresh_token(db, other_raw_token, expire_days=7)  # must not raise


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

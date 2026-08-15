import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RefreshToken


class RefreshTokenReuseDetected(Exception):
    """Raised when a refresh token that was already rotated away is
    presented again — a strong signal of token theft/replay. The caller
    is responsible for revoking every live token for user_id."""
    def __init__(self, user_id: int):
        self.user_id = user_id
        super().__init__(f"Refresh token reuse detected for user_id={user_id}")


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


async def create_refresh_token(db: AsyncSession, user_id: int, expire_days: int) -> tuple[str, RefreshToken]:
    raw_token = secrets.token_urlsafe(32)
    record = RefreshToken(
        user_id=user_id,
        token_hash=_hash_token(raw_token),
        expires_at=datetime.utcnow() + timedelta(days=expire_days),
    )
    db.add(record)
    await db.flush()
    return raw_token, record


async def rotate_refresh_token(db: AsyncSession, raw_token: str, expire_days: int) -> tuple[str, RefreshToken]:
    """Looks up raw_token, revokes it, and issues a new one for the same
    user, all in one commit. Raises ValueError if the token is unknown or
    expired. Raises RefreshTokenReuseDetected (after revoking every other
    live token for that user) if the token was already revoked before this
    call — see the reuse-detection note in the design spec."""
    token_hash = _hash_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    record = result.scalars().first()

    if record is None:
        raise ValueError("unknown refresh token")

    if record.revoked_at is not None:
        await revoke_all_for_user(db, record.user_id)
        raise RefreshTokenReuseDetected(record.user_id)

    if record.expires_at < datetime.utcnow():
        raise ValueError("expired refresh token")

    record.revoked_at = datetime.utcnow()
    new_raw_token, new_record = await create_refresh_token(db, record.user_id, expire_days)
    await db.commit()
    return new_raw_token, new_record


async def revoke_refresh_token(db: AsyncSession, raw_token: str) -> None:
    token_hash = _hash_token(raw_token)
    result = await db.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    record = result.scalars().first()
    if record is not None and record.revoked_at is None:
        record.revoked_at = datetime.utcnow()
        await db.commit()


async def revoke_all_for_user(db: AsyncSession, user_id: int) -> None:
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.utcnow())
    )
    await db.commit()

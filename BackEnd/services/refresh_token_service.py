import hashlib
import secrets
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import RefreshToken

# Cross-tab races: two tabs of the same browser can both hold the same
# refresh-token cookie value and both fire a refresh request before the
# browser's cookie jar has propagated the rotation from the first request's
# response. The second request then presents an already-rotated token,
# which is indistinguishable at the DB layer from a genuine replay of a
# stolen token — except by how recently the rotation happened. A real
# attacker replay realistically arrives well after the legitimate rotation
# (the token had to be captured and reused later); a sibling-tab race
# arrives within milliseconds to a couple seconds. REUSE_GRACE_WINDOW_SECONDS
# draws that line: a revocation younger than this is treated as a benign
# race (reject just this one request, no mass revocation); older is treated
# as a real reuse-detected event (revoke every live token for the user).
REUSE_GRACE_WINDOW_SECONDS = 30


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
        if (datetime.utcnow() - record.revoked_at).total_seconds() < REUSE_GRACE_WINDOW_SECONDS:
            # Likely a sibling-tab race against a token we ourselves just
            # rotated, not theft — reject this request only, don't nuke
            # every other live session for the user.
            raise ValueError("refresh token already rotated")
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

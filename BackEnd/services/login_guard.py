from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from database.models import User


def is_locked(user: User) -> bool:
    return user.locked_until is not None and user.locked_until > datetime.utcnow()


async def register_failed_attempt(db: AsyncSession, user: User, max_attempts: int, lockout_minutes: int) -> None:
    user.failed_login_attempts += 1
    if user.failed_login_attempts >= max_attempts:
        user.locked_until = datetime.utcnow() + timedelta(minutes=lockout_minutes)
        user.failed_login_attempts = 0
    await db.commit()


async def reset_failed_attempts(db: AsyncSession, user: User) -> None:
    if user.failed_login_attempts != 0 or user.locked_until is not None:
        user.failed_login_attempts = 0
        user.locked_until = None
        await db.commit()

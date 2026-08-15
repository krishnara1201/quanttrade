import threading
from datetime import datetime, timedelta

import bcrypt
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.security import OAuth2PasswordRequestForm
from jose import jwt as jose_jwt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db
from database.models import User
from services import login_guard, refresh_token_service
from services.auth_service import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    COOKIE_SECURE,
    LOGIN_LOCKOUT_MINUTES,
    LOGIN_MAX_ATTEMPTS,
    REFRESH_TOKEN_EXPIRE_DAYS,
    SECRET_KEY,
)
from services.rate_limiter import fixed_window

router = APIRouter(prefix="/api/auth", tags=["auth"])

REFRESH_COOKIE_NAME = "refresh_token"
REFRESH_COOKIE_PATH = "/api/auth"
LOGIN_IP_LIMIT_PER_MINUTE = 20

_login_ip_cache: dict[str, fixed_window] = {}
_login_ip_lock = threading.Lock()


def _check_login_ip_rate_limit(request: Request) -> None:
    """A tighter, route-scoped limiter separate from app.py's global
    100 req/60s middleware — blunts credential-stuffing sprays across many
    accounts from one IP before the per-account lockout below even applies."""
    ip_address = request.client.host
    with _login_ip_lock:
        if ip_address not in _login_ip_cache:
            _login_ip_cache[ip_address] = fixed_window(60, LOGIN_IP_LIMIT_PER_MINUTE)
        limiter = _login_ip_cache[ip_address]
    if not limiter.is_allowed(request.url.path):
        raise HTTPException(status_code=429, detail="Too many requests, please try again later")
    limiter.increment()


def _set_refresh_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE_NAME,
        value=raw_token,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
        max_age=REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(key=REFRESH_COOKIE_NAME, path=REFRESH_COOKIE_PATH)


class UserCreate(BaseModel):
    name: str
    email: EmailStr
    password: str

    class Config:
        extra = "forbid"  # reject unexpected fields like created_at sent as string


class Token(BaseModel):
    access_token: str
    token_type: str


@router.post("/")
async def create_user(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    # Use validated data dict to avoid passing strings for timestamps or other unexpected fields
    payload = user_data.dict(exclude={"password"})
    db_user = User(**payload)
    pw_bytes = user_data.password.encode("utf-8")
    if len(pw_bytes) > 72:
        raise HTTPException(status_code=400, detail="password too long for bcrypt (max 72 bytes); please use a shorter password")

    try:
        hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
        db_user.password_hash = hashed.decode("utf-8")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(db_user)
    try:
        await db.commit()
        await db.refresh(db_user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="email already exists")

    return db_user


@router.post("/token")
async def login_for_access_token(
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(_check_login_ip_rate_limit),
):
    result = await db.execute(select(User).where(User.email == form_data.username))
    user = result.scalars().first()

    if user is not None and login_guard.is_locked(user):
        raise HTTPException(
            status_code=429,
            detail="Account temporarily locked due to repeated failed login attempts. Try again later.",
        )

    password_ok = user is not None and bcrypt.checkpw(
        form_data.password.encode("utf-8"), user.password_hash.encode("utf-8")
    )

    if not password_ok:
        if user is not None:
            await login_guard.register_failed_attempt(db, user, LOGIN_MAX_ATTEMPTS, LOGIN_LOCKOUT_MINUTES)
        raise HTTPException(
            status_code=401,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    await login_guard.reset_failed_attempts(db, user)

    token = create_access_token(user.email, user.id, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    raw_refresh_token, _ = await refresh_token_service.create_refresh_token(db, user.id, REFRESH_TOKEN_EXPIRE_DAYS)
    await db.commit()

    _set_refresh_cookie(response, raw_refresh_token)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/refresh")
async def refresh_access_token(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(_check_login_ip_rate_limit),
):
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if not raw_token:
        raise HTTPException(status_code=401, detail="No refresh token provided")

    try:
        new_raw_token, record = await refresh_token_service.rotate_refresh_token(
            db, raw_token, REFRESH_TOKEN_EXPIRE_DAYS
        )
    except refresh_token_service.RefreshTokenReuseDetected:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Session invalidated, please log in again")
    except ValueError:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    result = await db.execute(select(User).where(User.id == record.user_id))
    user = result.scalars().first()
    if user is None:
        _clear_refresh_cookie(response)
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    token = create_access_token(user.email, user.id, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    _set_refresh_cookie(response, new_raw_token)
    return {"access_token": token, "token_type": "bearer"}


@router.post("/logout")
async def logout(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    raw_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if raw_token:
        await refresh_token_service.revoke_refresh_token(db, raw_token)
    _clear_refresh_cookie(response)
    return {"detail": "logged out"}


def create_access_token(username: str, user_id: int, expires_delta: timedelta):
    to_encode = {"sub": username, "user_id": user_id}
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jose_jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

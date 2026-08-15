from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db
from database.models import User
from jose import JWTError, jwt as jose_jwt
from dotenv import load_dotenv
import os

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY")
ALGORITHM = os.getenv("ALGORITHM")

ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "7"))
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "true").lower() == "true"
LOGIN_MAX_ATTEMPTS = int(os.getenv("LOGIN_MAX_ATTEMPTS", "5"))
LOGIN_LOCKOUT_MINUTES = int(os.getenv("LOGIN_LOCKOUT_MINUTES", "15"))

SECRET_KEY_PLACEHOLDER = "your-secret-key-here-change-this-in-production"


def validate_secret_key() -> None:
    """Fail fast at startup if SECRET_KEY is missing or still the
    .env.example placeholder, rather than booting and failing
    unpredictably per-request the first time get_current_user or
    create_access_token runs."""
    if not SECRET_KEY or SECRET_KEY == SECRET_KEY_PLACEHOLDER:
        raise RuntimeError(
            "SECRET_KEY is not set (or is still the example placeholder). "
            "Set a real secret in BackEnd/.env before starting the app."
        )


oauth2_bearer = OAuth2PasswordBearer(tokenUrl="api/auth/token")

async def get_current_user(token: str = Depends(oauth2_bearer), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=401,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jose_jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    result = await db.execute(
        select(User).where(User.email == username)
    )
    user = result.scalars().first()
    if user is None:
        raise credentials_exception
    return user

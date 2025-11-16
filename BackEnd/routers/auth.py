from typing import Optional, Annotated
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm, OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from database.models import User, Project
from database.connection import AsyncSessionLocal, get_db
import bcrypt
from datetime import datetime, timedelta
from jose import JWTError
from jose import jwt as jose_jwt
from services.auth_service import oauth2_bearer, SECRET_KEY, ALGORITHM, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

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
        # bcrypt.hashpw works with bytes; gensalt() returns a salt
        hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
        db_user.password_hash = hashed.decode("utf-8")
    except ValueError as e:
        # This can occur when the underlying bcrypt backend rejects the input (e.g. >72 bytes)
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Catch other unexpected backend errors and return a 400 with the message.
        raise HTTPException(status_code=400, detail=str(e))

    db.add(db_user)
    try:
        await db.commit()
        await db.refresh(db_user)
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="email already exists")

    # Return a sanitized response that excludes password_hash
    return db_user


@router.post("/token")
async def login_for_access_token(form_data:OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = await authenticate_user(form_data.username, form_data.password, db)
    if user is None:
        return {"error": "Invalid credentials"}
    token = create_access_token(user.email, user.id, timedelta(days=1))
    return {'access_token': token, 'token_type': 'bearer'}

async def authenticate_user(email: str, password: str, db: AsyncSession):
    result = await db.execute(
        select(User).where(User.email == email)
    )
    user = result.scalars().first()
    if user:
        # checkpw expects bytes
        if bcrypt.checkpw(password.encode('utf-8'), user.password_hash.encode('utf-8')):
            return user
    return None

def create_access_token(username: str, user_id: int, expires_delta: timedelta):
    to_encode = {"sub": username, "user_id": user_id}
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jose_jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


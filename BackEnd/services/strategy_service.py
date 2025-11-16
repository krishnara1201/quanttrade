from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from BackEnd.database.connection import get_db
from BackEnd.database.models import User
from jose import JWTError, jwt as jose_jwt
from BackEnd.services.auth_service import get_current_user


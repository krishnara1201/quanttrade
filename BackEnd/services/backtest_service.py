from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db
from database.models import User, Project, Strategy, MarketData, BacktestResult
from jose import JWTError, jwt as jose_jwt
from services.auth_service import get_current_user


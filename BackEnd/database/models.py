from sqlalchemy import (
    Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, Index, Boolean
)
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")
    

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    owner = relationship("User", back_populates="projects")
    created_at = Column(DateTime, default=datetime.utcnow)
    description = Column(Text)
    strategies = relationship("Strategy", back_populates="project", cascade="all, delete-orphan")
    

class Strategy(Base):
    __tablename__ = "strategies"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    project = relationship("Project", back_populates="strategies")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    parameters = Column(Text)  # JSON string of strategy parameters
    code = Column(Text)  # Strategy code as text (optional)
    status = Column(String, default="draft")  # e.g. active, inactive, backtesting
    is_public = Column(Boolean, default=False)  # visibility
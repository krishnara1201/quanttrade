from sqlalchemy import (
    Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, Index, Boolean, JSON
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
    backtest = relationship("BacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    
class MarketData(Base):
    __tablename__ = "market_data"
    id = Column(Integer, primary_key=True)
    ticker = Column(String, index=True, nullable=False)
    date = Column(DateTime, index=True, nullable=False)
    open = Column(String, nullable=False)
    high = Column(String, nullable=False)
    low = Column(String, nullable=False)
    close = Column(String, nullable=False)
    volume = Column(String, nullable=False)
    adj_close = Column(String, nullable=True)

    __table_args__ = (
        UniqueConstraint('ticker', 'date', name='uix_ticker_date'),
        Index('idx_ticker_date', 'ticker', 'date'),
    )
    
class BacktestResult(Base):
    __tablename__ = "backtest_results"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    strategy = relationship("Strategy", back_populates="backtest")
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    results = Column(JSON, default={})  # Summary stats, performance metrics stored as JSON
    trades = Column(JSON, default=[])  # List of trades executed, each with details (entry/exit, price, size)
    logs = Column(Text, default='')  # Optional logs or error messages
    created_at = Column(DateTime, default=datetime.utcnow)
    strategy = relationship("Strategy", back_populates="backtests")
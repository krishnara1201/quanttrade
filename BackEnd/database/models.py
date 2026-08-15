from sqlalchemy import (
    Column, Integer, String, DateTime, Text, ForeignKey, UniqueConstraint, Index, Boolean, JSON, Float
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
    failed_login_attempts = Column(Integer, default=0, nullable=False)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    projects = relationship("Project", back_populates="owner", cascade="all, delete-orphan")


class RefreshToken(Base):
    """A rotating, revocable refresh token backing the short-lived JWT access
    token — see routers/auth.py. Only the SHA-256 hash of the raw token is
    stored; the raw value only ever exists as an httpOnly cookie in transit."""
    __tablename__ = "refresh_tokens"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    token_hash = Column(String, unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)



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
    backtests = relationship("BacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    portfolio_backtests = relationship("PortfolioBacktestResult", back_populates="strategy", cascade="all, delete-orphan")
    walk_forward_backtests = relationship("WalkForwardBacktestResult", back_populates="strategy", cascade="all, delete-orphan")

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
    strategy = relationship("Strategy", back_populates="backtests")
    ticker = Column(String, nullable=True)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    initial_capital = Column(Float, nullable=True)
    commission_pct = Column(Float, nullable=True)
    slippage_pct = Column(Float, nullable=True)
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    results = Column(JSON, default={})  # Summary stats, performance metrics stored as JSON
    trades = Column(JSON, default=[])  # List of trades executed, each with details (entry/exit, price, size)
    signals = Column(JSON, default=[])  # Per-bar {date, close, signal} series for charting
    equity_curve = Column(JSON, default=[])  # Per-bar {date, equity} mark-to-market series
    benchmark_equity_curve = Column(JSON, default=[])  # Buy-and-hold {date, equity} reference series
    status = Column(String, default="pending")  # pending -> running -> success | failed
    error_message = Column(Text, nullable=True)
    logs = Column(Text, default='')  # Optional logs or error messages
    created_at = Column(DateTime, default=datetime.utcnow)


class PortfolioBacktestResult(Base):
    __tablename__ = "portfolio_backtest_results"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    strategy = relationship("Strategy", back_populates="portfolio_backtests")
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    initial_capital = Column(Float, nullable=False)
    commission_pct = Column(Float, nullable=False)
    slippage_pct = Column(Float, nullable=False)
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    allocations = Column(JSON, default=[])   # [{ticker, weight}] — normalized weights actually used
    results = Column(JSON, default={})       # aggregate portfolio metrics
    equity_curve = Column(JSON, default=[])  # aggregate portfolio {date, equity} series
    benchmark_equity_curve = Column(JSON, default=[])  # aggregate buy-and-hold {date, equity} reference series
    per_ticker = Column(JSON, default={})    # {ticker: {allocated_capital, metrics, trades, signals, equity_curve}}
    status = Column(String, default="pending")  # pending -> running -> success | failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class WalkForwardBacktestResult(Base):
    __tablename__ = "walk_forward_backtest_results"
    id = Column(Integer, primary_key=True)
    strategy_id = Column(Integer, ForeignKey("strategies.id"), nullable=False)
    strategy = relationship("Strategy", back_populates="walk_forward_backtests")
    ticker = Column(String, nullable=False)
    start_date = Column(DateTime, nullable=False)
    end_date = Column(DateTime, nullable=False)
    test_window_days = Column(Integer, nullable=False)
    initial_capital = Column(Float, nullable=False)
    commission_pct = Column(Float, nullable=False)
    slippage_pct = Column(Float, nullable=False)
    allow_short = Column(Boolean, default=False, nullable=False)
    stop_loss_pct = Column(Float, nullable=True)
    take_profit_pct = Column(Float, nullable=True)
    total_folds = Column(Integer, nullable=True)       # set once fold boundaries are computed
    folds_completed = Column(Integer, default=0, nullable=False)  # incremented per fold, for progress polling
    folds = Column(JSON, default=[])                    # [{fold_index, train_start, train_end, test_start, test_end, return_pct, num_trades}]
    trades = Column(JSON, default=[])                   # pooled across all fold test windows, chronological
    equity_curve = Column(JSON, default=[])              # stitched OOS curve, each row tagged fold_index
    benchmark_equity_curve = Column(JSON, default=[])    # buy-and-hold over the same stitched period
    results = Column(JSON, default={})                   # aggregate metrics, same field names as BacktestResult.results
    status = Column(String, default="pending")           # pending -> running -> success | failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class DataImportJob(Base):
    """Tracks an async CSV/Stooq upload or Alpha Vantage import — the
    market-data equivalent of BacktestResult's status/error_message, except
    there's no existing entity to attach status to for imports, hence a
    dedicated table. MarketData itself stays unowned (see routers/data.py);
    user_id here just tracks who submitted the job, so their own DataPage
    knows what to poll."""
    __tablename__ = "data_import_jobs"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    source = Column(String, nullable=False)  # "csv" | "alpha_vantage"
    ticker = Column(String, nullable=True)  # null for a multi-symbol Stooq .txt upload
    status = Column(String, default="pending")  # pending -> running -> success | failed
    result = Column(JSON, nullable=True)  # {ticker, inserted, skipped} or a list thereof
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

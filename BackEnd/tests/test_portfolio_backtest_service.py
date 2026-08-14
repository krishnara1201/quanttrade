"""Tests for portfolio-level backtests: basket diversification across
multiple tickers with custom fixed weights, aggregated into one portfolio
equity curve/metrics on top of the existing single-ticker StrategyExecutor.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, PortfolioBacktestResult, MarketData
from services.portfolio_backtest_service import normalize_weights, _check_ticker_coverage, aggregate_equity_curves, aggregate_portfolio_metrics


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_portfolio_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(name="strat", project_id=project.id, parameters="{}")
        db.add(strategy)
        await db.flush()

        pbr = PortfolioBacktestResult(
            strategy_id=strategy.id,
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            allocations=[{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}],
            results={"return_pct": 1.0},
            equity_curve=[{"date": "2024-01-01T00:00:00", "equity": 10000.0}],
            per_ticker={"AAPL": {"metrics": {}}},
        )
        db.add(pbr)
        await db.commit()
        await db.refresh(pbr)

        result = await db.execute(
            select(PortfolioBacktestResult).where(PortfolioBacktestResult.id == pbr.id)
        )
        loaded = result.scalars().first()
        assert loaded.allocations == [
            {"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}
        ]
        assert loaded.initial_capital == 10000.0
        assert loaded.commission_pct == 0.1
        assert loaded.per_ticker == {"AAPL": {"metrics": {}}}


def test_normalize_weights_scales_to_sum_one():
    result = normalize_weights([{"ticker": "AAPL", "weight": 2}, {"ticker": "MSFT", "weight": 1}])
    assert result[0] == {"ticker": "AAPL", "weight": pytest.approx(2 / 3)}
    assert result[1] == {"ticker": "MSFT", "weight": pytest.approx(1 / 3)}


def test_normalize_weights_already_summing_to_one_is_unchanged():
    result = normalize_weights([{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}])
    assert result == [{"ticker": "AAPL", "weight": 0.5}, {"ticker": "MSFT", "weight": 0.5}]


def test_normalize_weights_rejects_single_ticker():
    with pytest.raises(ValueError, match="at least 2 tickers"):
        normalize_weights([{"ticker": "AAPL", "weight": 1}])


def test_normalize_weights_rejects_non_positive_weight():
    with pytest.raises(ValueError, match="AAPL"):
        normalize_weights([{"ticker": "AAPL", "weight": 0}, {"ticker": "MSFT", "weight": 1}])


def test_normalize_weights_rejects_duplicate_ticker():
    with pytest.raises(ValueError, match="Duplicate"):
        normalize_weights([{"ticker": "AAPL", "weight": 1}, {"ticker": "AAPL", "weight": 1}])


async def _seed_market_data(db, ticker, dates_and_closes):
    for d, close in dates_and_closes:
        db.add(MarketData(
            ticker=ticker, date=d, open=str(close), high=str(close),
            low=str(close), close=str(close), volume="1000",
        ))
    await db.commit()


@pytest.mark.asyncio
async def test_check_ticker_coverage_passes_when_range_is_covered(session_factory):
    async with session_factory() as db:
        await _seed_market_data(db, "AAPL", [
            (datetime(2024, 1, 1), 10), (datetime(2024, 1, 10), 12),
        ])
        # Should not raise
        await _check_ticker_coverage("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 10), db)


@pytest.mark.asyncio
async def test_check_ticker_coverage_rejects_shorter_range(session_factory):
    async with session_factory() as db:
        await _seed_market_data(db, "AAPL", [
            (datetime(2024, 1, 3), 10), (datetime(2024, 1, 7), 12),
        ])
        with pytest.raises(ValueError, match="AAPL"):
            await _check_ticker_coverage("AAPL", datetime(2024, 1, 1), datetime(2024, 1, 10), db)


@pytest.mark.asyncio
async def test_check_ticker_coverage_rejects_missing_ticker(session_factory):
    async with session_factory() as db:
        with pytest.raises(ValueError, match="MSFT"):
            await _check_ticker_coverage("MSFT", datetime(2024, 1, 1), datetime(2024, 1, 10), db)


def test_aggregate_equity_curves_sums_matching_dates():
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}],
        "MSFT": [{"date": "d1", "equity": 200.0}, {"date": "d2", "equity": 190.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},
        {"date": "d2", "equity": 300.0},
    ]


def test_aggregate_equity_curves_forward_fills_missing_middle_date():
    # MSFT has no bar on "d2" (a bar-level gap) — its d1 value should carry forward
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}, {"date": "d3", "equity": 120.0}],
        "MSFT": [{"date": "d1", "equity": 200.0}, {"date": "d3", "equity": 205.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},
        {"date": "d2", "equity": 310.0},  # AAPL 110 + MSFT forward-filled 200
        {"date": "d3", "equity": 325.0},
    ]


def test_aggregate_equity_curves_uses_allocated_capital_before_first_point():
    # MSFT's curve starts at "d2" — "d1" should use its starting allocated_capital
    curves = {
        "AAPL": [{"date": "d1", "equity": 100.0}, {"date": "d2", "equity": 110.0}],
        "MSFT": [{"date": "d2", "equity": 205.0}],
    }
    result = aggregate_equity_curves(curves, {"AAPL": 100.0, "MSFT": 200.0})
    assert result == [
        {"date": "d1", "equity": 300.0},  # AAPL 100 + MSFT's uninvested 200
        {"date": "d2", "equity": 315.0},
    ]


def test_aggregate_portfolio_metrics_pools_trades_across_tickers():
    per_ticker_results = {
        "AAPL": {"trades": [
            {"type": "entry", "price": 100, "date": "d1", "size": 1},
            {"type": "exit", "price": 110, "date": "d2", "size": 1, "pnl": 10.0},
        ]},
        "MSFT": {"trades": [
            {"type": "entry", "price": 200, "date": "d1", "size": 1},
            {"type": "exit", "price": 190, "date": "d2", "size": 1, "pnl": -10.0},
        ]},
    }
    portfolio_equity_curve = [
        {"date": "d1", "equity": 1000.0},
        {"date": "d2", "equity": 1000.0},
    ]
    metrics = aggregate_portfolio_metrics(per_ticker_results, portfolio_equity_curve, 1000.0)

    assert metrics["num_trades"] == 2
    assert metrics["win_rate"] == pytest.approx(50.0)
    assert metrics["final_capital"] == pytest.approx(1000.0)
    assert metrics["total_return"] == pytest.approx(0.0)
    assert metrics["return_pct"] == pytest.approx(0.0)
    assert "max_drawdown_pct" in metrics
    assert "sharpe_ratio" in metrics


def test_aggregate_portfolio_metrics_handles_no_trades():
    portfolio_equity_curve = [{"date": "d1", "equity": 1000.0}]
    metrics = aggregate_portfolio_metrics({"AAPL": {"trades": []}}, portfolio_equity_curve, 1000.0)
    assert metrics["num_trades"] == 0
    assert metrics["win_rate"] == 0.0


from services import portfolio_backtest_service


@pytest_asyncio.fixture
async def portfolio_seeded(session_factory):
    """User/project/strategy plus two tickers of clean, hand-verifiable price data."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="strat",
            project_id=project.id,
            parameters=json.dumps({
                "name": "strat",
                "parameters": {},
                "rules": {"entry": "close > 0", "exit": "close < 0"},
            }),
        )
        db.add(strategy)
        await db.flush()

        # AAPL: flat at 100 the whole time (no entry signal ever fires -> stays in cash)
        for i in range(5):
            db.add(MarketData(
                ticker="AAPL", date=datetime(2024, 1, i + 1), open="100", high="100",
                low="100", close="100", volume="1000",
            ))
        # MSFT: same, flat at 200
        for i in range(5):
            db.add(MarketData(
                ticker="MSFT", date=datetime(2024, 1, i + 1), open="200", high="200",
                low="200", close="200", volume="1000",
            ))
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_run_portfolio_backtest_allocates_and_aggregates(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        response = await portfolio_backtest_service.run_portfolio_backtest(
            portfolio_seeded["strategy_id"],
            [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
            "2024-01-01", "2024-01-05",
            10000.0, 0.1, 0.05,
            db, user,
        )

    assert response["allocations"] == [
        {"ticker": "AAPL", "weight": pytest.approx(0.5)},
        {"ticker": "MSFT", "weight": pytest.approx(0.5)},
    ]
    # Neither ticker's "close > 0" entry rule ever fires against flat prices with no
    # prior bar transition here (rules mode only evaluates from index 1), so with a
    # constant entry condition true from bar 1 onward AAPL/MSFT do open a position;
    # the exact P&L isn't asserted — this test verifies orchestration and shape.
    assert set(response["per_ticker"].keys()) == {"AAPL", "MSFT"}
    assert response["per_ticker"]["AAPL"]["allocated_capital"] == pytest.approx(5000.0)
    assert response["per_ticker"]["MSFT"]["allocated_capital"] == pytest.approx(5000.0)
    assert len(response["equity_curve"]) == 5
    assert "return_pct" in response["metrics"]


@pytest.mark.asyncio
async def test_run_portfolio_backtest_rejects_insufficient_coverage(session_factory, portfolio_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, portfolio_seeded["user_id"])
        with pytest.raises(ValueError, match="AAPL"):
            await portfolio_backtest_service.run_portfolio_backtest(
                portfolio_seeded["strategy_id"],
                [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
                "2023-01-01", "2024-01-05",  # AAPL/MSFT data only starts 2024-01-01
                10000.0, 0.1, 0.05,
                db, user,
            )


@pytest.mark.asyncio
async def test_run_portfolio_backtest_does_not_raise_missing_greenlet_on_unauthorized(session_factory, portfolio_seeded):
    """Ownership-check regression, mirroring test_backtest_ownership.py: a user
    who doesn't own the project must get a clean error, not MissingGreenlet."""
    async with session_factory() as db:
        other_user = User(name="Bob", email="bob@example.com", password_hash="x")
        db.add(other_user)
        await db.commit()
        await db.refresh(other_user)

    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == other_user.id))
        bob = result.scalars().first()
        with pytest.raises(ValueError, match="Unauthorized"):
            await portfolio_backtest_service.run_portfolio_backtest(
                portfolio_seeded["strategy_id"],
                [{"ticker": "AAPL", "weight": 1}, {"ticker": "MSFT", "weight": 1}],
                "2024-01-01", "2024-01-05",
                10000.0, 0.1, 0.05,
                db, bob,
            )

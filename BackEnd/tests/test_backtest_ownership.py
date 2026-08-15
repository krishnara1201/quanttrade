"""
Regression tests for the known async-lazy-load bug documented in CLAUDE.md:

`strategy.project.owner_id` / `backtest.strategy.project` ownership checks in
services/backtest_service.py and routers/backtest.py lazily traverse SQLAlchemy
relationships with no eager-loading configured. Against a real async session
(here: aiosqlite, standing in for asyncpg) this raises MissingGreenlet because
attribute access is synchronous but the lazy load needs to issue IO.

These tests use a fresh session per "request" (mirroring get_db's
async with AsyncSessionLocal() as session pattern) so relationships are
never pre-populated, and go through the same select()-then-attribute-access
code paths as production.
"""
import json
from datetime import datetime

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import Base, User, Project, Strategy, BacktestResult, MarketData
from services import backtest_service
from routers import backtest as backtest_router


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(session_factory):
    """Create a user/project/strategy/backtest and return their ids."""
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
            parameters=json.dumps(
                {
                    "name": "strat",
                    "parameters": {"fast_ma": 2, "slow_ma": 3},
                    "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
                }
            ),
        )
        db.add(strategy)
        await db.flush()

        backtest = BacktestResult(
            strategy_id=strategy.id,
            ticker="TEST",
            start_date=datetime(2024, 1, 1),
            end_date=datetime(2024, 1, 5),
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            status="success",
            results={},
            trades=[],
            signals=[],
            equity_curve=[],
        )
        db.add(backtest)

        for i, close in enumerate([10, 11, 12, 13, 14, 15]):
            db.add(
                MarketData(
                    ticker="TEST",
                    date=datetime(2024, 1, i + 1),
                    open=str(close),
                    high=str(close),
                    low=str(close),
                    close=str(close),
                    volume="1000",
                )
            )

        await db.commit()
        return {
            "user_id": user.id,
            "project_id": project.id,
            "strategy_id": strategy.id,
            "backtest_id": backtest.id,
        }


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest.mark.asyncio
async def test_create_and_execute_backtest_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
        )
        assert record.status == "pending"
        await backtest_service.execute_backtest(record.id, db)
        await db.refresh(record)
    assert record.status == "success"
    assert record.strategy_id == seeded["strategy_id"]
    assert record.results["num_trades"] >= 0


@pytest.mark.asyncio
async def test_execute_backtest_queries_market_data_with_datetime_not_raw_string(session_factory, seeded, monkeypatch):
    """Regression for a prod-only bug: start_date/end_date arrive as plain
    strings ("2024-01-01") at create_pending_backtest, parsed to datetime and
    persisted on the row (record.start_date/record.end_date). execute_backtest
    then queries MarketData using those already-parsed datetimes. SQLite's
    DateTime bind processor tolerates a raw str, so this bug would slip past a
    SQLite-only test unless it inspects the bound *type* directly — see the
    dialect-sensitivity note in CLAUDE.md."""
    captured_params = []

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
        )

        original_execute = db.execute

        async def spying_execute(stmt, *args, **kwargs):
            if hasattr(stmt, "compile"):
                compiled = stmt.compile(compile_kwargs={"literal_binds": False})
                captured_params.append(dict(compiled.params))
            return await original_execute(stmt, *args, **kwargs)

        monkeypatch.setattr(db, "execute", spying_execute)

        await backtest_service.execute_backtest(record.id, db)

    all_values = [v for params in captured_params for v in params.values()]
    assert "2024-01-01" not in all_values, "start_date leaked into the query as a raw string"
    assert "2024-01-06" not in all_values, "end_date leaked into the query as a raw string"
    assert any(
        isinstance(v, datetime) and v == datetime(2024, 1, 1) for v in all_values
    ), "expected start_date to be bound as a parsed datetime"


@pytest.mark.asyncio
async def test_get_backtest_results_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        results = await backtest_service.get_backtest_results(
            seeded["strategy_id"], db=db, user=user,
        )
    assert len(results) == 1
    assert results[0]["strategy_id"] == seeded["strategy_id"]


@pytest.mark.asyncio
async def test_get_backtest_detail_does_not_raise_missing_greenlet(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        detail = await backtest_router.get_backtest_detail(
            seeded["backtest_id"], db=db, user=user,
        )
    assert detail["id"] == seeded["backtest_id"]


@pytest.mark.asyncio
async def test_run_backtest_endpoint_marks_row_failed_when_delay_raises(session_factory, seeded, monkeypatch):
    """The BacktestResult row is already committed as 'pending' before
    run_backtest_task.delay(...) is called. If the broker (Redis) is
    unreachable at that moment, .delay() raises — without this fix that
    would propagate as an unhandled exception (-> generic 500) and leave the
    already-committed row stuck at status='pending' forever, with nothing
    ever picking it up. Simulate a broker outage by making .delay() raise,
    and assert the row is marked failed with a message and the endpoint
    raises a 503 instead of an opaque 500."""
    from fastapi import HTTPException
    from routers.backtest import BacktestRequest, run_backtest_task

    def broken_delay(*args, **kwargs):
        raise ConnectionError("could not connect to redis")

    monkeypatch.setattr(run_backtest_task, "delay", broken_delay)

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        req = BacktestRequest(
            strategy_id=seeded["strategy_id"], ticker="TEST",
            start_date="2024-01-01", end_date="2024-01-06",
        )
        with pytest.raises(HTTPException) as exc_info:
            await backtest_router.run_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 503

        result = await db.execute(
            select(BacktestResult).where(BacktestResult.strategy_id == seeded["strategy_id"])
        )
        records = result.scalars().all()
        newest = max(records, key=lambda r: r.id)

    assert newest.status == "failed"
    assert "Could not enqueue" in newest.error_message


@pytest.mark.asyncio
async def test_create_pending_backtest_persists_allow_short_and_stop_loss_take_profit(session_factory, seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user,
            allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
    assert record.allow_short is True
    assert record.stop_loss_pct == 2.5
    assert record.take_profit_pct == 5.0


@pytest.mark.asyncio
async def test_create_pending_backtest_rejects_non_positive_stop_loss_pct(session_factory, seeded):
    from fastapi import HTTPException

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        with pytest.raises(HTTPException) as exc_info:
            await backtest_service.create_pending_backtest(
                seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
                db=db, user=user, stop_loss_pct=0,
            )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_execute_backtest_passes_allow_short_and_stop_loss_take_profit_to_executor(session_factory, seeded, monkeypatch):
    from services.strategy_executor import StrategyExecutor
    captured = {}
    original_backtest = StrategyExecutor.backtest

    def spying_backtest(self, df, **kwargs):
        captured.update(kwargs)
        return original_backtest(self, df, **kwargs)

    monkeypatch.setattr(StrategyExecutor, "backtest", spying_backtest)

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        record = await backtest_service.create_pending_backtest(
            seeded["strategy_id"], "TEST", "2024-01-01", "2024-01-06",
            db=db, user=user, allow_short=True, stop_loss_pct=2.5, take_profit_pct=5.0,
        )
        await backtest_service.execute_backtest(record.id, db)

    assert captured["allow_short"] is True
    assert captured["stop_loss_pct"] == 2.5
    assert captured["take_profit_pct"] == 5.0


@pytest.mark.asyncio
async def test_execute_backtest_persists_benchmark_equity_curve(session_factory, seeded):
    async with session_factory() as db:
        await backtest_service.execute_backtest(seeded["backtest_id"], db)

    async with session_factory() as db:
        result = await db.execute(select(BacktestResult).where(BacktestResult.id == seeded["backtest_id"]))
        record = result.scalars().first()

    # seeded fixture: closes [10, 11, 12, 13, 14] for Jan 1-5 2024 (end_date
    # is Jan 5), initial_capital 10000.0 -> 1000 shares bought at close=10.
    assert record.status == "success"
    assert len(record.benchmark_equity_curve) == 5
    assert record.benchmark_equity_curve[0]["equity"] == pytest.approx(10000.0)
    assert record.benchmark_equity_curve[-1]["equity"] == pytest.approx(14000.0)


@pytest.mark.asyncio
async def test_run_walk_forward_endpoint_rejects_rules_mode_strategy(session_factory, seeded):
    """`seeded`'s strategy is rules-mode (fast_ma/slow_ma) — walk-forward
    should reject it with a 400, not attempt to enqueue anything."""
    from fastapi import HTTPException
    from routers.backtest import WalkForwardBacktestRequest, run_walk_forward_backtest_endpoint

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        req = WalkForwardBacktestRequest(
            strategy_id=seeded["strategy_id"], ticker="TEST",
            start_date="2015-01-01", end_date="2020-01-01", test_window_days=180,
        )
        with pytest.raises(HTTPException) as exc_info:
            await run_walk_forward_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 400
        assert "custom-code strategy" in exc_info.value.detail


@pytest.mark.asyncio
async def test_run_walk_forward_endpoint_marks_row_failed_when_apply_async_raises(session_factory, seeded, monkeypatch):
    """Same enqueue-failure contract as the other three async task types
    (see routers/backtest.py and routers/data.py) — if the broker is
    unreachable, the already-committed pending row is marked failed and the
    endpoint raises a 503 instead of an opaque 500."""
    import json
    from fastapi import HTTPException
    from sqlalchemy import select as _select
    from database.models import Project, Strategy
    from routers.backtest import WalkForwardBacktestRequest, run_walk_forward_backtest_endpoint, walk_forward_task

    # seeded's own strategy is rules-mode (see the rejection test above) —
    # build a sibling custom-code strategy in the same project so this test
    # can exercise the enqueue-failure path instead of the mode check.
    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        proj_result = await db.execute(_select(Project).where(Project.owner_id == user.id))
        project = proj_result.scalars().first()
        strategy = Strategy(
            name="ml", project_id=project.id,
            parameters=json.dumps({"name": "ml", "mode": "custom_code"}),
            code="def generate_signals(df):\n    return df['close'] * 0\n",
        )
        db.add(strategy)
        await db.commit()
        await db.refresh(strategy)

    def broken_apply_async(*args, **kwargs):
        raise ConnectionError("could not connect to redis")

    monkeypatch.setattr(walk_forward_task, "apply_async", broken_apply_async)

    async with session_factory() as db:
        user = await _reload_user(session_factory, seeded["user_id"])
        req = WalkForwardBacktestRequest(
            strategy_id=strategy.id, ticker="TEST",
            start_date="2015-01-01", end_date="2020-01-01", test_window_days=180,
        )
        with pytest.raises(HTTPException) as exc_info:
            await run_walk_forward_backtest_endpoint(req, db=db, user=user)
        assert exc_info.value.status_code == 503

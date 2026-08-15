"""Tests for walk-forward (expanding-window out-of-sample) backtest
evaluation: fold boundary computation, per-fold execution with capital
compounding, and the buy-and-hold benchmark overlay. No mocks — real
in-memory sqlite+aiosqlite, real sandboxed strategy execution, matching
this repo's existing testing style (see
docs/superpowers/specs/2026-08-14-walk-forward-backtesting-design.md).
"""
import json
from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from database.models import (
    Base, User, Project, Strategy, WalkForwardBacktestResult, MarketData,
)


@pytest_asyncio.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    import worker_db
    monkeypatch.setattr(worker_db, "_session_factory", factory)

    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_walk_forward_backtest_result_round_trips(session_factory):
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="strat", project_id=project.id,
            parameters=json.dumps({"name": "strat", "mode": "custom_code"}),
            code="def generate_signals(df):\n    return df['close'] * 0\n",
        )
        db.add(strategy)
        await db.flush()

        wfr = WalkForwardBacktestResult(
            strategy_id=strategy.id,
            ticker="AAPL",
            start_date=datetime(2015, 1, 1),
            end_date=datetime(2020, 1, 1),
            test_window_days=180,
            initial_capital=10000.0,
            commission_pct=0.1,
            slippage_pct=0.05,
            total_folds=5,
            folds_completed=2,
            folds=[{"fold_index": 0, "return_pct": 1.5}],
            trades=[{"type": "entry", "price": 100.0}],
            equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0, "fold_index": 0}],
            benchmark_equity_curve=[{"date": "2016-01-01T00:00:00", "equity": 10000.0}],
            results={"return_pct": 5.0},
        )
        db.add(wfr)
        await db.commit()
        await db.refresh(wfr)

        result = await db.execute(
            select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == wfr.id)
        )
        loaded = result.scalars().first()
        assert loaded.ticker == "AAPL"
        assert loaded.test_window_days == 180
        assert loaded.total_folds == 5
        assert loaded.folds_completed == 2
        assert loaded.folds == [{"fold_index": 0, "return_pct": 1.5}]
        assert loaded.benchmark_equity_curve == [{"date": "2016-01-01T00:00:00", "equity": 10000.0}]
        assert loaded.status == "pending"  # default


from services.walk_forward_service import compute_fold_boundaries, estimate_fold_count


def test_compute_fold_boundaries_raises_when_range_too_short():
    with pytest.raises(ValueError, match="too short"):
        compute_fold_boundaries(datetime(2024, 1, 1), datetime(2024, 6, 1), test_window_days=90)


def test_compute_fold_boundaries_produces_contiguous_expanding_folds():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    folds = compute_fold_boundaries(start, end, test_window_days=180)

    assert len(folds) >= 5
    for i, fold in enumerate(folds):
        assert fold["fold_index"] == i
        assert fold["train_start"] == start
        assert fold["train_end"] == fold["test_start"] - timedelta(days=1)
        assert (fold["test_end"] - fold["test_start"]).days == 179  # 180-day inclusive window
        assert fold["test_end"] <= end
        if i > 0:
            assert fold["test_start"] == folds[i - 1]["test_end"] + timedelta(days=1)
            assert fold["train_end"] > folds[i - 1]["train_end"]  # expanding


def test_compute_fold_boundaries_drops_short_trailing_remainder():
    start = datetime(2015, 1, 1)
    # 365 (min train) + 180 (one full fold) + a 50-day remainder, too short
    # for a second 180-day fold -> exactly 1 fold, remainder dropped.
    end = start + timedelta(days=365) + timedelta(days=179) + timedelta(days=50)
    folds = compute_fold_boundaries(start, end, test_window_days=180)
    assert len(folds) == 1
    assert folds[0]["test_end"] < end


def test_estimate_fold_count_is_a_conservative_upper_bound():
    start = datetime(2015, 1, 1)
    end = datetime(2020, 1, 1)
    actual = len(compute_fold_boundaries(start, end, test_window_days=180))
    estimate = estimate_fold_count(start, end, test_window_days=180)
    assert estimate >= actual


from services import walk_forward_service


async def _reload_user(session_factory, user_id):
    async with session_factory() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalars().first()


@pytest_asyncio.fixture
async def custom_code_strategy_seeded(session_factory):
    """User/project/custom-code strategy plus enough clean daily bars for
    multiple walk-forward folds (5 years of calendar days)."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="ml-strat", project_id=project.id,
            parameters=json.dumps({"name": "ml-strat", "mode": "custom_code"}),
            code=(
                "def generate_signals(df):\n"
                "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
                "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
                "    return up - down\n"
            ),
        )
        db.add(strategy)
        await db.flush()

        rules_strategy = Strategy(
            name="rules-strat", project_id=project.id,
            parameters=json.dumps({
                "name": "rules-strat", "parameters": {},
                "rules": {"entry": "close > 0", "exit": "close < 0"},
            }),
        )
        db.add(rules_strategy)
        await db.flush()

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close=str(100 + (i % 10)), volume="1000",
            ))
        await db.commit()
        return {
            "user_id": user.id,
            "custom_code_strategy_id": strategy.id,
            "rules_strategy_id": rules_strategy.id,
        }


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_persists_row(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
    assert record.status == "pending"
    assert record.ticker == "AAPL"
    assert record.test_window_days == 180
    assert record.folds_completed == 0


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_rejects_rules_mode_strategy(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        with pytest.raises(ValueError, match="custom-code strategy"):
            await walk_forward_service.create_pending_walk_forward_backtest(
                custom_code_strategy_seeded["rules_strategy_id"], "AAPL",
                "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
            )


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_rejects_too_small_test_window(session_factory, custom_code_strategy_seeded):
    """test_window_days=1 over a multi-year range would produce thousands of
    sequential sandboxed subprocess launches and a Celery time limit measured
    in hours from a single API request — reject anything under 30 days."""
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        with pytest.raises(ValueError, match="at least 30"):
            await walk_forward_service.create_pending_walk_forward_backtest(
                custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
                "2015-01-01", "2020-01-01", 1, 10000.0, 0.1, 0.05, db, user,
            )


@pytest.mark.asyncio
async def test_create_pending_walk_forward_backtest_rejects_unauthorized_user(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        other_user = User(name="Eve", email="eve@example.com", password_hash="x")
        db.add(other_user)
        await db.commit()
        await db.refresh(other_user)

        with pytest.raises(ValueError, match="Unauthorized"):
            await walk_forward_service.create_pending_walk_forward_backtest(
                custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
                "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, other_user,
            )


@pytest.mark.asyncio
async def test_get_walk_forward_backtest_results_and_detail_do_not_raise_missing_greenlet(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )

    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        results = await walk_forward_service.get_walk_forward_backtest_results(
            custom_code_strategy_seeded["custom_code_strategy_id"], db, user,
        )
        assert len(results) == 1
        assert results[0]["ticker"] == "AAPL"

    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        detail = await walk_forward_service.get_walk_forward_backtest_detail(record.id, db, user)
        assert detail["id"] == record.id
        assert detail["status"] == "pending"


@pytest.mark.asyncio
async def test_execute_walk_forward_marks_row_failed_when_no_market_data(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "MSFT",  # no MarketData seeded for MSFT
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "No market data" in loaded.error_message


@pytest.mark.asyncio
async def test_execute_walk_forward_marks_row_failed_when_range_too_short(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2015-06-01", 90, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "too short" in loaded.error_message


@pytest.mark.asyncio
async def test_execute_walk_forward_end_to_end_compounds_capital_and_tracks_progress(session_factory, custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()

    assert loaded.status == "success"
    assert loaded.total_folds >= 5
    assert loaded.folds_completed == loaded.total_folds
    assert len(loaded.folds) == loaded.total_folds
    for i, fold in enumerate(loaded.folds):
        assert fold["fold_index"] == i
        assert "return_pct" in fold
        assert "num_trades" in fold
    # equity curve is continuous across folds and tagged with fold_index
    assert all("fold_index" in point for point in loaded.equity_curve)
    assert loaded.equity_curve[0]["fold_index"] == 0
    assert loaded.equity_curve[-1]["fold_index"] == loaded.total_folds - 1
    # benchmark curve covers the same stitched OOS period
    assert len(loaded.benchmark_equity_curve) > 0
    assert loaded.benchmark_equity_curve[0]["date"] == loaded.equity_curve[0]["date"]
    assert "return_pct" in loaded.results
    assert "sharpe_ratio" in loaded.results


@pytest.mark.asyncio
async def test_execute_walk_forward_second_fold_starts_from_first_folds_ending_capital(session_factory, custom_code_strategy_seeded):
    """Numerically pins the capital-compounding invariant: fold 1's starting
    capital must equal fold 0's actual ending equity, not
    record.initial_capital. A regression that reset
    fold_start_capital = record.initial_capital every iteration (instead of
    carrying running_capital forward) would still pass every structural
    assertion in the end-to-end test above -- this test exists specifically
    to falsify that regression."""
    async with session_factory() as db:
        user = await _reload_user(session_factory, custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            custom_code_strategy_seeded["custom_code_strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()

    assert loaded.status == "success"
    fold0_return_pct = loaded.folds[0]["return_pct"]
    # Sanity check: the oscillating-price fixture actually trades in fold 0,
    # so its return isn't trivially zero -- otherwise this test couldn't
    # distinguish "compounds correctly" from "resets every fold".
    assert fold0_return_pct != pytest.approx(0.0, abs=1e-9)

    expected_fold1_start_capital = loaded.initial_capital * (1 + fold0_return_pct / 100)
    # If this equaled initial_capital, fold 0 would have to have had exactly
    # 0% return -- already ruled out above, so this is a redundant but
    # cheap belt-and-suspenders check that the two quantities really differ.
    assert expected_fold1_start_capital != pytest.approx(loaded.initial_capital, rel=1e-6)

    fold1_points = [p for p in loaded.equity_curve if p["fold_index"] == 1]
    assert fold1_points, "fold 1 should have produced equity-curve points"
    first_fold1_equity = fold1_points[0]["equity"]

    # The first equity point of fold 1 is computed from fold 1's starting
    # cash (fold_start_capital = running_capital carried over from fold 0)
    # possibly adjusted by a same-bar trade's commission/slippage -- hence
    # approx rather than exact equality.
    assert first_fold1_equity == pytest.approx(expected_fold1_start_capital, rel=0.05)
    # And directly rules out the reset-to-initial-capital regression: fold
    # 1's first equity point must NOT equal the original initial_capital.
    assert first_fold1_equity != pytest.approx(loaded.initial_capital, rel=1e-6)


@pytest_asyncio.fixture
async def broken_custom_code_strategy_seeded(session_factory):
    """A custom-code strategy whose generate_signals() always raises —
    used to verify a fold failure fails the whole walk-forward run with a
    message naming which fold failed."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="broken", project_id=project.id,
            parameters=json.dumps({"name": "broken", "mode": "custom_code"}),
            code="def generate_signals(df):\n    raise ValueError('boom')\n",
        )
        db.add(strategy)
        await db.flush()

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open="100", high="101", low="99", close="100", volume="1000",
            ))
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


@pytest.mark.asyncio
async def test_execute_walk_forward_fails_whole_run_naming_the_fold(session_factory, broken_custom_code_strategy_seeded):
    async with session_factory() as db:
        user = await _reload_user(session_factory, broken_custom_code_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            broken_custom_code_strategy_seeded["strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()
    assert loaded.status == "failed"
    assert "Fold 1" in loaded.error_message
    assert loaded.folds_completed == 0


# The literal example strategy shown to users in StrategyBuilder.jsx's
# custom-code section, under the collapsible "Example: walk-forward-ready
# strategy" panel — copied verbatim so this test exercises the exact code path a
# real user would paste in, not a simplified stand-in. Deliberately avoids
# `import pandas`/`import numpy` (both disallowed in the sandbox) and only
# uses `df`'s own methods, same as every other custom-code fixture in this
# file, but this is the one fixture that actually fits/predicts with a real
# sklearn model per fold inside the resource-limited sandbox subprocess.
LOGISTIC_REGRESSION_EXAMPLE_CODE = """from sklearn.linear_model import LogisticRegression

def generate_signals(df):
    df = df.copy()
    df['return_1d'] = df['close'].pct_change()
    df['sma_10'] = df['close'].rolling(10).mean()
    df['sma_30'] = df['close'].rolling(30).mean()
    df['momentum'] = df['close'] / df['close'].shift(10) - 1
    df['target'] = (df['close'].shift(-1) > df['close']).astype(int)

    features = ['return_1d', 'sma_10', 'sma_30', 'momentum']
    train = df.dropna(subset=features + ['target'])

    # Walk-forward calls this fresh each fold with a growing df --
    # early folds may not have enough rows yet. Stay flat rather than
    # fit on too little data.
    if len(train) < 50:
        return df['close'] * 0

    model = LogisticRegression()
    model.fit(train[features], train['target'])

    predictable = df.dropna(subset=features)
    preds = model.predict(predictable[features])  # 0/1

    signal = df['close'] * 0
    signal.loc[predictable.index] = preds * 2 - 1  # -> -1/1
    return signal
"""


@pytest_asyncio.fixture
async def logistic_regression_strategy_seeded(session_factory):
    """User/project/custom-code strategy running the literal sklearn
    LogisticRegression example shown in StrategyBuilder.jsx's custom-code
    section, plus 5 years of daily bars — enough for multiple expanding
    folds. Prices oscillate (not flat/monotonic) so the model actually has
    varying features/targets to fit on each fold."""
    async with session_factory() as db:
        user = User(name="Ada", email="ada@example.com", password_hash="x")
        db.add(user)
        await db.flush()
        project = Project(name="proj", owner_id=user.id)
        db.add(project)
        await db.flush()
        strategy = Strategy(
            name="logreg-strat", project_id=project.id,
            parameters=json.dumps({"name": "logreg-strat", "mode": "custom_code"}),
            code=LOGISTIC_REGRESSION_EXAMPLE_CODE,
        )
        db.add(strategy)
        await db.flush()

        start = datetime(2015, 1, 1)
        for i in range(365 * 5):
            close = 100 + 10 * ((i % 20) - 10) / 10 + (i % 3)
            db.add(MarketData(
                ticker="AAPL", date=start + timedelta(days=i),
                open=str(close), high=str(close + 1), low=str(close - 1),
                close=str(close), volume="1000",
            ))
        await db.commit()
        return {"user_id": user.id, "strategy_id": strategy.id}


@pytest.mark.asyncio
async def test_execute_walk_forward_end_to_end_with_real_sklearn_logistic_regression(
    session_factory, logistic_regression_strategy_seeded
):
    """True end-to-end test: the exact LogisticRegression example users see
    in the UI, run across multiple real folds, each fold spawning a real
    sandboxed subprocess that fits/predicts a fresh model. Every other
    custom-code fixture in this file uses a trivial up/down momentum diff
    strategy, so this is the only test that exercises the feature's actual
    purpose (repeatedly refitting an ML model per fold in the sandbox).
    Expect this to take noticeably longer than a typical unit test — that's
    expected, matching this repo's no-mocks testing philosophy."""
    async with session_factory() as db:
        user = await _reload_user(session_factory, logistic_regression_strategy_seeded["user_id"])
        record = await walk_forward_service.create_pending_walk_forward_backtest(
            logistic_regression_strategy_seeded["strategy_id"], "AAPL",
            "2015-01-01", "2020-01-01", 180, 10000.0, 0.1, 0.05, db, user,
        )
        await walk_forward_service.execute_walk_forward(record.id, db)

    async with session_factory() as db:
        result = await db.execute(select(WalkForwardBacktestResult).where(WalkForwardBacktestResult.id == record.id))
        loaded = result.scalars().first()

    assert loaded.status == "success", loaded.error_message
    assert loaded.total_folds >= 2
    assert loaded.folds_completed == loaded.total_folds
    assert len(loaded.folds) == loaded.total_folds

    for key in ("total_return", "return_pct", "final_capital", "win_rate", "num_trades",
                "max_drawdown_pct", "sharpe_ratio"):
        assert key in loaded.results

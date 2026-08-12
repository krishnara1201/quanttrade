import pandas as pd
import pytest

from services.strategy_executor import StrategyExecutor


def make_price_df(closes):
    dates = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    df = pd.DataFrame({"close": closes}, index=dates)
    return df


def make_executor(parameters=None, entry="close > 0", exit="close < 0"):
    config = {
        "name": "test",
        "parameters": parameters or {},
        "rules": {"entry": entry, "exit": exit},
    }
    return StrategyExecutor(config)


def test_sma_matches_pandas_rolling_mean():
    closes = [10, 11, 12, 13, 14, 15, 16, 17]
    df = make_price_df(closes)
    executor = make_executor({"fast_ma": 3})
    executor._calculate_indicators(df, {"fast_ma": 3})
    expected = pd.Series(closes, index=df.index).rolling(window=3).mean()
    pd.testing.assert_series_equal(df["fast_ma"], expected, check_names=False)


def test_rsi_known_values():
    # Monotonically increasing closes -> RSI should approach 100 (no losses)
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25]
    df = make_price_df(closes)
    executor = make_executor({"rsi_period": 14})
    executor._calculate_indicators(df, {"rsi_period": 14})
    last_rsi = df["rsi"].iloc[-1]
    assert last_rsi == pytest.approx(100.0, abs=1e-6)


def test_bollinger_bands_match_manual_calc():
    closes = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19]
    df = make_price_df(closes)
    executor = make_executor({"bb_period": 5, "bb_std": 2})
    executor._calculate_indicators(df, {"bb_period": 5, "bb_std": 2})
    mid = pd.Series(closes, index=df.index).rolling(window=5).mean()
    std = pd.Series(closes, index=df.index).rolling(window=5).std()
    pd.testing.assert_series_equal(df["bb_mid"], mid, check_names=False)
    pd.testing.assert_series_equal(df["bb_upper"], mid + 2 * std, check_names=False)
    pd.testing.assert_series_equal(df["bb_lower"], mid - 2 * std, check_names=False)


def test_ema_matches_pandas_ewm():
    closes = [10, 11, 12, 13, 14, 15, 16, 17, 18, 19]
    df = make_price_df(closes)
    executor = make_executor({"ema_period": 4})
    executor._calculate_indicators(df, {"ema_period": 4})
    expected = pd.Series(closes, index=df.index).ewm(span=4, adjust=False).mean()
    pd.testing.assert_series_equal(df["ema"], expected, check_names=False)


def test_macd_matches_manual_ema_calc():
    closes = [10, 12, 11, 13, 15, 14, 16, 18, 17, 19, 21, 20, 22, 24, 23]
    df = make_price_df(closes)
    executor = make_executor({"macd_fast": 3, "macd_slow": 6, "macd_signal": 2})
    executor._calculate_indicators(df, {"macd_fast": 3, "macd_slow": 6, "macd_signal": 2})

    price = pd.Series(closes, index=df.index)
    fast_ema = price.ewm(span=3, adjust=False).mean()
    slow_ema = price.ewm(span=6, adjust=False).mean()
    expected_macd = fast_ema - slow_ema
    expected_signal = expected_macd.ewm(span=2, adjust=False).mean()
    expected_hist = expected_macd - expected_signal

    pd.testing.assert_series_equal(df["macd"], expected_macd, check_names=False)
    pd.testing.assert_series_equal(df["macd_signal_line"], expected_signal, check_names=False)
    pd.testing.assert_series_equal(df["macd_hist"], expected_hist, check_names=False)


def test_execute_trades_applies_capital_sizing_commission_and_slippage():
    closes = [100, 100, 110]
    df = make_price_df(closes)
    df["signal"] = [0, 1, -1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=1000.0, commission_pct=1.0, slippage_pct=0.5
    )

    assert len(trades) == 2
    entry, exit_ = trades

    assert entry["type"] == "entry"
    assert entry["size"] == 9
    assert entry["price"] == pytest.approx(100.5, abs=1e-6)

    assert exit_["type"] == "exit"
    assert exit_["size"] == 9
    assert exit_["price"] == pytest.approx(109.45, abs=1e-6)
    assert exit_["pnl"] == pytest.approx(61.6545, abs=1e-3)

    assert len(equity_curve) == 3
    assert equity_curve[0]["equity"] == pytest.approx(1000.0, abs=1e-6)
    assert equity_curve[1]["equity"] == pytest.approx(986.455, abs=1e-3)
    assert equity_curve[2]["equity"] == pytest.approx(1061.6545, abs=1e-3)


def test_execute_trades_skips_entry_when_cash_cannot_afford_one_share():
    closes = [1000, 1000, 1100]
    df = make_price_df(closes)
    df["signal"] = [0, 1, -1]
    executor = make_executor()

    trades, equity_curve = executor._execute_trades(
        df, initial_capital=500.0, commission_pct=1.0, slippage_pct=0.5
    )

    assert trades == []
    assert all(pt["equity"] == pytest.approx(500.0) for pt in equity_curve)


def test_backtest_returns_row_record_signals_and_equity_curve():
    closes = [100, 101, 99, 102, 105, 103, 108]
    df = make_price_df(closes)
    config = {
        "name": "sma-cross",
        "parameters": {"fast_ma": 2, "slow_ma": 4},
        "rules": {"entry": "fast_ma > slow_ma", "exit": "fast_ma < slow_ma"},
    }
    executor = StrategyExecutor(config)

    result = executor.backtest(df, initial_capital=1000.0, commission_pct=0.1, slippage_pct=0.05)

    assert set(result.keys()) == {"trades", "metrics", "signals", "equity_curve"}
    assert len(result["signals"]) == len(closes)
    assert len(result["equity_curve"]) == len(closes)
    for row in result["signals"]:
        assert set(row.keys()) == {"date", "close", "signal"}
    for row in result["equity_curve"]:
        assert set(row.keys()) == {"date", "equity"}

    # Regression guard: a prior bug in the signal-assignment loop (a `.loc`
    # based form) silently corrupted the DataFrame rather than raising,
    # producing a df of all-zero signals with the right shape. Assert the
    # signals/trades actually reflect real strategy activity, not just shape.
    assert any(row["signal"] != 0 for row in result["signals"])
    assert len(result["trades"]) >= 1


def test_max_drawdown_pct_known_curve():
    equity_values = [1000, 1050, 1020, 1100, 950, 1080]
    equity_curve = [{"date": str(i), "equity": v} for i, v in enumerate(equity_values)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df(equity_values), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    expected_drawdown = (1100 - 950) / 1100 * 100
    assert metrics["max_drawdown_pct"] == pytest.approx(expected_drawdown, rel=1e-6)


def test_sharpe_ratio_matches_manual_formula():
    equity_values = [1000, 1050, 1020, 1100, 950, 1080]
    equity_curve = [{"date": str(i), "equity": v} for i, v in enumerate(equity_values)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df(equity_values), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    returns = pd.Series(equity_values).pct_change().dropna()
    expected_sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)
    assert metrics["sharpe_ratio"] == pytest.approx(expected_sharpe, rel=1e-6)


def test_sharpe_ratio_zero_when_no_volatility():
    equity_curve = [{"date": str(i), "equity": 1000.0} for i in range(5)]
    executor = make_executor()

    metrics = executor._calculate_metrics(
        df=make_price_df([1000] * 5), trades=[], initial_capital=1000.0, equity_curve=equity_curve
    )

    assert metrics["sharpe_ratio"] == 0.0
    assert metrics["max_drawdown_pct"] == 0.0


def test_backtest_custom_code_mode_reuses_execute_trades_and_metrics():
    closes = [100, 101, 99, 102, 105, 103, 108]
    df = make_price_df(closes)
    code = (
        "def generate_signals(df):\n"
        "    up = (df['close'] > df['close'].shift(1)).astype(int)\n"
        "    down = (df['close'] < df['close'].shift(1)).astype(int)\n"
        "    return up - down\n"
    )
    executor = StrategyExecutor({"name": "custom", "mode": "custom_code"}, code=code)

    result = executor.backtest(df, initial_capital=1000.0, commission_pct=0.1, slippage_pct=0.05)

    assert set(result.keys()) == {"trades", "metrics", "signals", "equity_curve"}
    assert len(result["signals"]) == len(closes)
    assert len(result["equity_curve"]) == len(closes)
    assert any(row["signal"] != 0 for row in result["signals"])
    assert len(result["trades"]) >= 1


def test_validate_rejects_custom_code_mode_without_code():
    executor = StrategyExecutor({"name": "custom", "mode": "custom_code"})
    with pytest.raises(ValueError):
        executor.validate()


def test_validate_still_requires_rules_for_default_mode():
    executor = StrategyExecutor({"name": "test"})
    with pytest.raises(ValueError):
        executor.validate()


def test_metrics_use_equity_curve_final_value_when_position_still_open():
    # Entry rule fires on bar 1 and never exits (all closes positive), so trades
    # contains only an ENTRY with no matching EXIT. total_return/final_capital
    # must reflect the mark-to-market equity curve, not just realized exit P&L.
    closes = [100, 101, 99, 102, 105, 103, 108]
    df = make_price_df(closes)
    executor = make_executor(entry="close > 0", exit="close < 0")

    result = executor.backtest(df, initial_capital=1000.0, commission_pct=0.1, slippage_pct=0.05)

    trades = result["trades"]
    metrics = result["metrics"]
    equity_curve = result["equity_curve"]

    assert any(t["type"] == "entry" for t in trades)
    assert not any(t["type"] == "exit" for t in trades)

    expected_final_capital = equity_curve[-1]["equity"]
    assert expected_final_capital != pytest.approx(1000.0)
    assert metrics["final_capital"] == pytest.approx(expected_final_capital, rel=1e-9)
    assert metrics["total_return"] == pytest.approx(expected_final_capital - 1000.0, rel=1e-9)

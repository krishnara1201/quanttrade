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

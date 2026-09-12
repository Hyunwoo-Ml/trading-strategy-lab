import numpy as np
import pandas as pd
import pytest

from sw1.indicators.technical import (
    add_bollinger_bands,
    add_macd,
    add_moving_average_cross,
    add_rsi,
    add_volume_profile,
    compute_all_technical_indicators,
)


def test_rsi_bounded_between_0_and_100(mock_ohlcv):
    out = add_rsi(mock_ohlcv)
    valid = out["RSI"].dropna()
    assert len(valid) > 0
    assert (valid >= 0).all() and (valid <= 100).all()


def test_rsi_requires_warmup_period(mock_ohlcv):
    out = add_rsi(mock_ohlcv, period=14)
    assert out["RSI"].iloc[:13].isna().all()


def test_macd_histogram_equals_macd_minus_signal(mock_ohlcv):
    out = add_macd(mock_ohlcv)
    diff = out["MACD"] - out["MACD_SIGNAL"] - out["MACD_HIST"]
    assert np.allclose(diff.dropna(), 0, atol=1e-9)


def test_bollinger_upper_always_above_lower(mock_ohlcv):
    out = add_bollinger_bands(mock_ohlcv)
    valid = out.dropna(subset=["BB_UPPER", "BB_LOWER"])
    assert (valid["BB_UPPER"] >= valid["BB_LOWER"]).all()


def test_bollinger_pct_b_reflects_price_position(mock_ohlcv):
    out = add_bollinger_bands(mock_ohlcv)
    at_mid = out.dropna(subset=["BB_PCT_B"])
    near_upper = at_mid[at_mid["Close"] >= at_mid["BB_UPPER"] * 0.999]
    if len(near_upper) > 0:
        assert (near_upper["BB_PCT_B"] >= 0.9).all()


def test_moving_average_cross_labels_are_valid(mock_ohlcv):
    out = add_moving_average_cross(mock_ohlcv, short_window=10, long_window=30)
    labels = set(out["MA_CROSS"].dropna().unique())
    assert labels <= {"golden", "dead"}


def test_moving_average_cross_rejects_bad_window_order(mock_ohlcv):
    with pytest.raises(ValueError):
        add_moving_average_cross(mock_ohlcv, short_window=200, long_window=50)


def test_volume_ratio_is_positive(mock_ohlcv):
    out = add_volume_profile(mock_ohlcv)
    valid = out["VOL_RATIO"].dropna()
    assert (valid > 0).all()


def test_compute_all_indicators_adds_expected_columns(mock_ohlcv):
    out = compute_all_technical_indicators(mock_ohlcv)
    expected = {
        "RSI", "MACD", "MACD_SIGNAL", "MACD_HIST",
        "BB_MID", "BB_UPPER", "BB_LOWER", "BB_PCT_B",
        "MA_50", "MA_200", "MA_CROSS",
        "VOL_MA_20", "VOL_RATIO",
    }
    assert expected <= set(out.columns)
    assert len(out) == len(mock_ohlcv)


def test_missing_columns_raise_value_error():
    bad_df = pd.DataFrame({"Close": [1, 2, 3]})
    with pytest.raises(ValueError):
        add_rsi(bad_df)


def test_empty_dataframe_raises_value_error():
    empty = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
    with pytest.raises(ValueError):
        add_rsi(empty)


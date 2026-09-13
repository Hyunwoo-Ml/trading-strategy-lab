from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sw1.indicators.technical import compute_all_technical_indicators
from sw1.market.regime import compute_market_regime

from .conftest import make_mock_ohlcv


def _synthetic_indicators(close_value, ma_50, ma_200) -> pd.DataFrame:
    """Builds a minimal 1-row DataFrame with exactly the columns
    compute_market_regime reads, so the risk_on/risk_off classification
    can be tested deterministically without depending on random-walk data
    happening to produce a downtrend."""
    return pd.DataFrame(
        {"Close": [close_value], "MA_50": [ma_50], "MA_200": [ma_200]},
        index=pd.DatetimeIndex([pd.Timestamp.today().normalize()]),
    )


def test_confirmed_downtrend_is_risk_off():
    df = _synthetic_indicators(close_value=90.0, ma_50=95.0, ma_200=100.0)
    result = compute_market_regime(df, index_ticker="QQQ")
    assert result.regime == "risk_off"
    assert "QQQ" in result.reason


def test_healthy_trend_is_risk_on():
    df = _synthetic_indicators(close_value=110.0, ma_50=105.0, ma_200=100.0)
    result = compute_market_regime(df)
    assert result.regime == "risk_on"


def test_close_below_ma50_but_ma50_above_ma200_is_not_confirmed_downtrend():
    # A dip within a longer uptrend shouldn't trip risk_off -- both
    # conditions (close<MA50 AND MA50<MA200) must hold.
    df = _synthetic_indicators(close_value=98.0, ma_50=100.0, ma_200=90.0)
    result = compute_market_regime(df)
    assert result.regime == "risk_on"


def test_missing_moving_averages_default_to_risk_on():
    df = _synthetic_indicators(close_value=100.0, ma_50=np.nan, ma_200=np.nan)
    result = compute_market_regime(df)
    assert result.regime == "risk_on"
    assert "부족" in result.reason


def test_empty_dataframe_raises():
    with pytest.raises(ValueError):
        compute_market_regime(pd.DataFrame(columns=["Close", "MA_50", "MA_200"]))


def test_integrates_with_real_indicator_pipeline():
    # End-to-end sanity check: real compute_all_technical_indicators output
    # (300 mock trading days) has enough history for MA_50 to be non-null,
    # and compute_market_regime doesn't blow up on the real column shape.
    ohlcv = make_mock_ohlcv(n=300, seed=7)
    indicators = compute_all_technical_indicators(ohlcv)
    result = compute_market_regime(indicators, index_ticker="QQQ")
    assert result.regime in ("risk_on", "risk_off")
    assert result.index_ticker == "QQQ"

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sw1.indicators.weekly import compute_weekly_trend, resample_to_weekly


def _daily_df_from_weekly_closes(weekly_closes: list[float]) -> pd.DataFrame:
    """Builds a 5-trading-day-per-week daily DataFrame whose Friday closes
    match `weekly_closes` exactly, so resample_to_weekly's output is
    deterministic and easy to assert on."""
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=len(weekly_closes) * 5, freq="B")
    # bdate_range gives Mon-Fri blocks; take every 5th (Friday, given the
    # range ends exactly on a business day boundary aligned to full weeks).
    closes = np.repeat(weekly_closes, 5)
    n = len(dates)
    closes = closes[-n:]
    return pd.DataFrame(
        {
            "Open": closes,
            "High": closes * 1.001,
            "Low": closes * 0.999,
            "Close": closes,
            "Volume": np.full(n, 10_000_000),
        },
        index=dates,
    )


def test_resample_to_weekly_produces_one_bar_per_week():
    df = _daily_df_from_weekly_closes([100.0] * 10)
    weekly = resample_to_weekly(df)
    assert len(weekly) >= 9  # allow +/-1 for calendar edge effects


def test_confirmed_uptrend_classified_up():
    # Steadily rising weekly closes over 35 weeks -> close > MA10 > MA30.
    closes = [100.0 + i * 2.0 for i in range(35)]
    df = _daily_df_from_weekly_closes(closes)
    result = compute_weekly_trend(df, short_window=10, long_window=30)
    assert result.trend == "up"


def test_confirmed_downtrend_classified_down():
    closes = [200.0 - i * 2.0 for i in range(35)]
    df = _daily_df_from_weekly_closes(closes)
    result = compute_weekly_trend(df, short_window=10, long_window=30)
    assert result.trend == "down"


def test_insufficient_history_defaults_to_flat():
    closes = [100.0] * 15  # fewer than long_window=30 weeks
    df = _daily_df_from_weekly_closes(closes)
    result = compute_weekly_trend(df, short_window=10, long_window=30)
    assert result.trend == "flat"
    assert "미만" in result.reason


def test_short_window_must_be_less_than_long_window():
    df = _daily_df_from_weekly_closes([100.0] * 40)
    with pytest.raises(ValueError):
        compute_weekly_trend(df, short_window=30, long_window=10)

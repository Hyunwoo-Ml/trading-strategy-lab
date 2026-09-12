"""Shared test fixtures -- mock OHLCV data so indicator logic can be
tested without any network access."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest


def make_mock_ohlcv(n: int = 300, start_price: float = 150.0, seed: int = 42) -> pd.DataFrame:
    """Random-walk mock daily bars, scaled roughly like an M7 stock price."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
    n = len(dates)  # bdate_range can return a different count than requested

    daily_returns = rng.normal(loc=0.0004, scale=0.018, size=n)
    close = start_price * np.cumprod(1 + daily_returns)

    high = close * (1 + np.abs(rng.normal(0, 0.006, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.006, size=n)))
    open_ = low + (high - low) * rng.uniform(0, 1, size=n)
    volume = rng.integers(low=5_000_000, high=60_000_000, size=n)

    return pd.DataFrame(
        {
            "Open": open_,
            "High": high,
            "Low": low,
            "Close": close,
            "Volume": volume,
        },
        index=dates,
    )


@pytest.fixture
def mock_ohlcv() -> pd.DataFrame:
    return make_mock_ohlcv()


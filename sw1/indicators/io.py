"""
SW1 -- read data/indicators/{TICKER}.csv back with a usable date index.

scripts/collect_daily_data.py writes these files from yfinance's
timezone-aware (America/New_York) index, so each date is stored with its
UTC offset ("2026-11-13 00:00:00-05:00"). A year of history always crosses
a daylight-saving switch, which mixes -05:00 and -04:00 offsets in one
column -- and `pd.read_csv(..., parse_dates=True)` gives up on mixed
offsets and silently leaves the index as plain strings. Anything that then
needs real dates (sw1.indicators.weekly's weekly resample, the criteria
snapshot date) fails or prints the raw string.

read_indicators_csv() parses that index into tz-naive New York market
dates (2026-11-13 00:00:00), the same calendar dates the CSV shows, so
weekly bars still end on the right Friday. Files written without offsets
(tests, older data) parse as-is.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

MARKET_TZ = "America/New_York"

_OFFSET_SUFFIX = re.compile(r"(?:[+-]\d{2}:?\d{2}|Z)$")


def to_market_dates(index: pd.Index) -> pd.DatetimeIndex:
    """Converts a date index (strings or datetimes, with or without UTC
    offsets) to tz-naive New York market dates."""
    if isinstance(index, pd.DatetimeIndex):
        if index.tz is not None:
            return index.tz_convert(MARKET_TZ).tz_localize(None)
        return index

    values = pd.Index(index).astype(str)
    if values.str.contains(_OFFSET_SUFFIX).any():
        return pd.DatetimeIndex(pd.to_datetime(values, utc=True)).tz_convert(MARKET_TZ).tz_localize(None)
    return pd.DatetimeIndex(pd.to_datetime(values))


def read_indicators_csv(path: str | Path) -> pd.DataFrame:
    """pd.read_csv for data/indicators/{TICKER}.csv with the date index
    parsed by to_market_dates(). Raises whatever read_csv/to_datetime
    raise -- callers already wrap this in their own graceful-degradation
    try/except."""
    df = pd.read_csv(path, index_col=0)
    df.index = to_market_dates(df.index)
    return df

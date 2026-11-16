from __future__ import annotations

import pandas as pd

from sw1.indicators.io import read_indicators_csv, to_market_dates
from sw1.indicators.weekly import compute_weekly_trend

from .conftest import make_mock_ohlcv


def _write_like_collect_daily_data(tmp_path, n=300):
    """Writes an indicators-shaped CSV the way scripts/collect_daily_data.py
    does in production: a tz-aware America/New_York index, which over a
    year spans a DST switch and so mixes -05:00 and -04:00 offsets."""
    df = make_mock_ohlcv(n=n)
    df.index = df.index.tz_localize("America/New_York")
    df.index.name = "Date"
    path = tmp_path / "AAPL.csv"
    df.to_csv(path)
    return path, df


def test_mixed_dst_offsets_parse_to_market_dates(tmp_path):
    path, original = _write_like_collect_daily_data(tmp_path)
    text = path.read_text()
    assert "-05:00" in text and "-04:00" in text  # the case that broke parse_dates=True

    df = read_indicators_csv(path)

    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.tz is None
    # Same calendar dates as written -- no shift to the previous day from the UTC round trip.
    assert list(df.index) == list(original.index.tz_localize(None))


def test_weekly_trend_works_on_production_shaped_file(tmp_path):
    path, _ = _write_like_collect_daily_data(tmp_path)
    trend = compute_weekly_trend(read_indicators_csv(path))
    assert trend.trend in {"up", "down", "flat"}
    assert pd.Timestamp(trend.date).dayofweek == 4  # weekly bars still end on Friday


def test_offset_free_dates_are_left_as_is(tmp_path):
    df = make_mock_ohlcv(n=50)
    path = tmp_path / "MSFT.csv"
    df.to_csv(path)

    out = read_indicators_csv(path)

    assert list(out.index) == list(df.index)


def test_tz_aware_datetime_index_is_converted():
    idx = pd.DatetimeIndex(["2026-11-13 05:00:00+00:00"])
    assert list(to_market_dates(idx)) == [pd.Timestamp("2026-11-13 00:00:00")]

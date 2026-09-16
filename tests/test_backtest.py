from datetime import date, timedelta

import pandas as pd
import pytest

from sw1.validation.backtest import PeriodReturn, bucket_equity_by_period, total_return


def _equity_df(rows: list[tuple[str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["date", "equity"])
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")


# -- bucket_equity_by_period --


def test_empty_equity_df_returns_no_periods():
    assert bucket_equity_by_period(pd.DataFrame(columns=["equity"])) == []


def test_single_quarter_single_row():
    df = _equity_df([("2024-02-01", 105_000.0)])
    results = bucket_equity_by_period(df, freq="Q")
    assert len(results) == 1
    r = results[0]
    assert r.period == "2024Q1"
    # only one observation -- start and end both fall back to that same row
    assert r.start_equity == 105_000.0
    assert r.end_equity == 105_000.0
    assert r.period_return == pytest.approx(0.0)


def test_two_quarters_chain_from_prior_close_not_series_start():
    df = _equity_df(
        [
            ("2024-01-15", 100_000.0),
            ("2024-03-28", 110_000.0),  # Q1 close: +10%
            ("2024-04-05", 110_000.0),  # Q2 open
            ("2024-06-20", 121_000.0),  # Q2 close: +10% AGAIN, chained off Q1's 110k, not the original 100k
        ]
    )
    results = bucket_equity_by_period(df, freq="Q")
    assert [r.period for r in results] == ["2024Q1", "2024Q2"]

    q1, q2 = results
    assert q1.start_equity == pytest.approx(100_000.0)
    assert q1.end_equity == pytest.approx(110_000.0)
    assert q1.period_return == pytest.approx(0.10)

    # Q2 must start from Q1's ending equity, not from the series' very first value
    assert q2.start_equity == pytest.approx(110_000.0)
    assert q2.end_equity == pytest.approx(121_000.0)
    assert q2.period_return == pytest.approx(0.10)


def test_negative_period_return_for_a_losing_quarter():
    df = _equity_df([("2024-01-02", 100_000.0), ("2024-03-29", 90_000.0)])
    results = bucket_equity_by_period(df, freq="Q")
    assert results[0].period_return == pytest.approx(-0.10)


def test_period_with_zero_start_equity_returns_none_not_raise():
    df = _equity_df([("2024-01-02", 0.0), ("2024-02-01", 500.0)])
    results = bucket_equity_by_period(df, freq="Q")
    assert results[0].period_return is None


def test_missing_periods_are_absent_not_null_filled():
    # a quarter with zero trading days in it (e.g. the model hadn't started
    # yet) must simply not appear -- not show up as a period_return=None row.
    df = _equity_df([("2024-01-15", 100_000.0), ("2024-09-10", 105_000.0)])
    results = bucket_equity_by_period(df, freq="Q")
    assert [r.period for r in results] == ["2024Q1", "2024Q3"]


def test_monthly_and_yearly_freq_produce_expected_labels():
    df = _equity_df([("2024-01-15", 100_000.0), ("2024-02-20", 101_000.0)])
    monthly = bucket_equity_by_period(df, freq="M")
    assert [r.period for r in monthly] == ["2024-01", "2024-02"]

    yearly_df = _equity_df([("2023-06-01", 100_000.0), ("2024-06-01", 108_000.0)])
    yearly = bucket_equity_by_period(yearly_df, freq="Y")
    assert [r.period for r in yearly] == ["2023", "2024"]


def test_to_dict_has_expected_keys():
    r = PeriodReturn(
        period="2024Q1", start_date="2024-01-01", end_date="2024-03-29",
        start_equity=100_000.0, end_equity=110_000.0, period_return=0.10,
    )
    d = r.to_dict()
    assert set(d.keys()) == {"period", "start_date", "end_date", "start_equity", "end_equity", "period_return"}


# -- total_return --


def test_total_return_empty_is_none():
    assert total_return(pd.DataFrame(columns=["equity"])) is None


def test_total_return_basic():
    df = _equity_df([("2021-01-04", 100_000.0), ("2023-12-29", 150_000.0)])
    assert total_return(df) == pytest.approx(0.50)


def test_total_return_zero_start_is_none():
    df = _equity_df([("2021-01-04", 0.0), ("2023-12-29", 500.0)])
    assert total_return(df) is None

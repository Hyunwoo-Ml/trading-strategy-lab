from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from sw1.validation.walkforward import (
    MIN_OBS_PER_WINDOW,
    WindowResult,
    compute_forward_returns,
    make_rolling_windows,
    run_walkforward,
    summarize_walkforward,
    window_ic,
)


def _date_index(n, start=date(2024, 1, 1)):
    return pd.to_datetime([start + timedelta(days=i) for i in range(n)])


# -- compute_forward_returns --


def test_compute_forward_returns_basic():
    close = pd.Series([100.0, 110.0, 121.0], index=_date_index(3))
    fwd = compute_forward_returns(close, horizon_days=1)
    assert fwd.iloc[0] == pytest.approx(0.10)
    assert fwd.iloc[1] == pytest.approx(0.10)


def test_compute_forward_returns_tail_is_nan():
    close = pd.Series([100.0, 110.0, 121.0], index=_date_index(3))
    fwd = compute_forward_returns(close, horizon_days=1)
    assert pd.isna(fwd.iloc[-1])


def test_compute_forward_returns_rejects_nonpositive_horizon():
    close = pd.Series([100.0, 110.0], index=_date_index(2))
    with pytest.raises(ValueError):
        compute_forward_returns(close, horizon_days=0)


# -- make_rolling_windows --


def test_make_rolling_windows_empty_input():
    assert make_rolling_windows([], window_days=30, step_days=10) == []


def test_make_rolling_windows_covers_full_range():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(100)]
    windows = make_rolling_windows(dates, window_days=30, step_days=30)
    assert windows[0][0] == date(2024, 1, 1)
    # every date should fall inside at least one window
    for d in dates:
        assert any(start <= d < end for start, end in windows)


def test_make_rolling_windows_last_window_clipped_not_dropped():
    dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(40)]
    windows = make_rolling_windows(dates, window_days=30, step_days=30)
    last_start, last_end = windows[-1]
    # clipped to just past the real last date (2024-02-09), not padded to a full 30 days
    assert last_end == date(2024, 2, 9) + timedelta(days=1)


def test_make_rolling_windows_rejects_nonpositive_args():
    with pytest.raises(ValueError):
        make_rolling_windows([date(2024, 1, 1)], window_days=0, step_days=10)
    with pytest.raises(ValueError):
        make_rolling_windows([date(2024, 1, 1)], window_days=10, step_days=0)


# -- window_ic --


def test_window_ic_perfect_positive_correlation():
    idx = _date_index(20)
    scores = pd.Series(range(20), index=idx, dtype=float)
    returns = pd.Series(range(20), index=idx, dtype=float)  # perfectly monotonic with scores
    ic, p, n = window_ic(scores, returns, min_obs=5)
    assert ic == pytest.approx(1.0)
    assert n == 20
    assert p is not None


def test_window_ic_perfect_negative_correlation():
    idx = _date_index(20)
    scores = pd.Series(range(20), index=idx, dtype=float)
    returns = pd.Series(list(reversed(range(20))), index=idx, dtype=float)
    ic, p, n = window_ic(scores, returns, min_obs=5)
    assert ic == pytest.approx(-1.0)


def test_window_ic_insufficient_obs_returns_none():
    idx = _date_index(3)
    scores = pd.Series([0.1, 0.2, 0.3], index=idx)
    returns = pd.Series([0.01, 0.02, 0.03], index=idx)
    ic, p, n = window_ic(scores, returns, min_obs=MIN_OBS_PER_WINDOW)
    assert ic is None
    assert p is None
    assert n == 3


def test_window_ic_drops_nan_pairs_before_counting():
    idx = _date_index(12)
    scores = pd.Series([float(i) for i in range(12)], index=idx)
    returns = pd.Series([float(i) for i in range(12)], index=idx)
    returns.iloc[:4] = np.nan  # only 8 valid pairs remain
    ic, p, n = window_ic(scores, returns, min_obs=5)
    assert n == 8
    assert ic == pytest.approx(1.0)


def test_window_ic_constant_series_returns_none():
    idx = _date_index(15)
    scores = pd.Series([0.5] * 15, index=idx)  # zero variance -> undefined correlation
    returns = pd.Series(range(15), index=idx, dtype=float)
    ic, p, n = window_ic(scores, returns, min_obs=5)
    assert ic is None


# -- run_walkforward --


def test_run_walkforward_one_result_per_window():
    idx = _date_index(90)
    scores = pd.Series(np.random.RandomState(0).randn(90), index=idx)
    returns = pd.Series(np.random.RandomState(1).randn(90), index=idx)
    results = run_walkforward(scores, returns, window_days=30, step_days=30, min_obs=5)
    expected_windows = make_rolling_windows([d.date() for d in idx], 30, 30)
    assert len(results) == len(expected_windows)
    assert all(isinstance(r, WindowResult) for r in results)


def test_run_walkforward_marks_thin_windows_as_unscored():
    idx = _date_index(90)
    scores = pd.Series(np.random.RandomState(0).randn(90), index=idx)
    returns = pd.Series(np.random.RandomState(1).randn(90), index=idx)
    # min_obs higher than any single window can supply -> every window unscored
    results = run_walkforward(scores, returns, window_days=10, step_days=10, min_obs=1000)
    assert all(r.ic is None for r in results)


# -- summarize_walkforward --


def test_summarize_walkforward_all_positive():
    results = [
        WindowResult(start=date(2024, 1, 1), end=date(2024, 2, 1), n_obs=20, ic=0.3, p_value=0.01),
        WindowResult(start=date(2024, 2, 1), end=date(2024, 3, 1), n_obs=20, ic=0.5, p_value=0.01),
    ]
    summary = summarize_walkforward(results)
    assert summary["n_windows_scored"] == 2
    assert summary["pct_positive"] == pytest.approx(1.0)
    assert summary["mean_ic"] == pytest.approx(0.4)
    assert summary["worst_ic"] == pytest.approx(0.3)
    assert summary["best_ic"] == pytest.approx(0.5)


def test_summarize_walkforward_mixed_signs():
    results = [
        WindowResult(start=date(2024, 1, 1), end=date(2024, 2, 1), n_obs=20, ic=0.2),
        WindowResult(start=date(2024, 2, 1), end=date(2024, 3, 1), n_obs=20, ic=-0.4),
    ]
    summary = summarize_walkforward(results)
    assert summary["pct_positive"] == pytest.approx(0.5)
    assert summary["mean_ic"] == pytest.approx(-0.1)


def test_summarize_walkforward_no_scored_windows():
    results = [
        WindowResult(start=date(2024, 1, 1), end=date(2024, 2, 1), n_obs=2, ic=None),
    ]
    summary = summarize_walkforward(results)
    assert summary["n_windows_scored"] == 0
    assert summary["n_windows_skipped_insufficient_data"] == 1
    assert summary["mean_ic"] is None
    assert summary["pct_positive"] is None


def test_window_result_to_dict_is_json_shaped():
    r = WindowResult(start=date(2024, 1, 1), end=date(2024, 2, 1), n_obs=20, ic=0.3, p_value=0.02)
    d = r.to_dict()
    assert d == {
        "start": "2024-01-01",
        "end": "2024-02-01",
        "n_obs": 20,
        "ic": 0.3,
        "p_value": 0.02,
    }

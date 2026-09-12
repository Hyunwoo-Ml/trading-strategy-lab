import pandas as pd
import pytest

from sw2.compare import compare_all_pairs, compare_daily_returns


def test_raises_with_fewer_than_two_observations():
    with pytest.raises(ValueError):
        compare_daily_returns("a", pd.Series([0.01]), "b", pd.Series([0.01, 0.02]))


def test_drops_nan_before_comparing():
    # first value is NaN (as equity_df's first daily_return always is) --
    # shouldn't count toward the n>=2 requirement or the mean
    a = pd.Series([None, 0.01, 0.02])
    b = pd.Series([None, 0.01, 0.02])
    result = compare_daily_returns("a", a, "b", b)
    assert result.n_a == 2
    assert result.n_b == 2


def test_clearly_different_means_are_detected():
    a = pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5)
    b = pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5)
    result = compare_daily_returns("winner", a, "loser", b)
    assert result.mean_daily_return_a > result.mean_daily_return_b
    assert result.significant_at_5pct


def test_compare_all_pairs_skips_pairs_without_enough_history():
    returns_by_model = {
        "a": pd.Series([0.01, 0.02, 0.03]),
        "b": pd.Series([0.01]),  # not enough history yet
        "c": pd.Series([-0.01, -0.02, -0.03]),
    }
    results = compare_all_pairs(returns_by_model)
    pairs = {(r.model_a, r.model_b) for r in results}
    assert ("a", "c") in pairs
    assert not any("b" in pair for pair in pairs)


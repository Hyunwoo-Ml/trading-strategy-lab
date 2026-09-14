import numpy as np
import pandas as pd
import pytest

from sw2.compare import (
    adjust_for_multiple_comparisons,
    block_bootstrap_pvalue,
    compare_all_pairs,
    compare_daily_returns,
)


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
    # Task #23: effect size should be large and positive (a's mean is
    # far above b's, relative to the pooled spread).
    assert result.cohens_d is not None
    assert result.cohens_d > 1.0


def test_cohens_d_sign_flips_with_comparison_order():
    a = pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5)
    b = pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5)
    a_vs_b = compare_daily_returns("a", a, "b", b)
    b_vs_a = compare_daily_returns("b", b, "a", a)
    assert a_vs_b.cohens_d == pytest.approx(-b_vs_a.cohens_d)


def test_zero_variance_both_groups_gives_none_not_nan():
    # both models made zero trades -- equity is flat every day, so daily
    # returns are exactly 0.0 for both groups. Welch's t-test is a 0/0
    # division here (undefined, not "no difference"), so this must come
    # back as None, never a raw NaN (data/sw2/comparisons/latest.json is
    # written from this and a bare `NaN` token is invalid JSON).
    a = pd.Series([0.0, 0.0, 0.0])
    b = pd.Series([0.0, 0.0, 0.0])
    result = compare_daily_returns("no_trades_a", a, "no_trades_b", b)
    assert result.t_statistic is None
    assert result.p_value is None
    assert result.significant_at_5pct is False
    # Task #23: pooled variance is also exactly zero here, so Cohen's d is
    # equally undefined -- same None-not-NaN treatment.
    assert result.cohens_d is None


def test_zero_variance_result_is_json_serializable_without_nan():
    import json
    from dataclasses import asdict

    a = pd.Series([0.0, 0.0])
    b = pd.Series([0.0, 0.0])
    result = compare_daily_returns("a", a, "b", b)
    encoded = json.dumps(asdict(result), allow_nan=False)
    assert "NaN" not in encoded
    assert json.loads(encoded)["t_statistic"] is None
    assert json.loads(encoded)["p_value"] is None


def test_compare_all_pairs_skips_pairs_without_enough_history():
    returns_by_model = {
        "a": pd.Series([0.01, 0.02, 0.03]),
        "b": pd.Series([0.01]),  # not enough history yet
        "c": pd.Series([-0.01, -0.02, -0.03]),
    }
    results = compare_all_pairs(returns_by_model, correction_method=None)
    pairs = {(r.model_a, r.model_b) for r in results}
    assert ("a", "c") in pairs
    assert not any("b" in pair for pair in pairs)


# ---------------------------------------------------------------------------
# Task #23: autocorrelation caveat (Ljung-Box)
# ---------------------------------------------------------------------------


def test_autocorrelation_warning_none_with_too_few_observations():
    # Below the min_obs threshold (10), there isn't enough data for a
    # Ljung-Box test to say anything meaningful -- None, not a guess.
    a = pd.Series([0.01, 0.02, 0.03, -0.01, 0.0])
    b = pd.Series([0.01, 0.02, 0.03, -0.01, 0.0])
    result = compare_daily_returns("a", a, "b", b)
    assert result.autocorrelation_warning_a is None
    assert result.autocorrelation_warning_b is None


def test_autocorrelation_warning_true_for_a_strong_trend():
    # A monotonic trend is about as serially correlated as a series gets --
    # Ljung-Box should flag it well below p<0.05.
    trending = pd.Series([0.001 * i for i in range(1, 16)])
    flat_noise = pd.Series([0.0030, -0.0104, 0.0075, 0.0094, -0.0195, -0.0130, 0.0013, -0.0032, -0.0002, -0.0085, 0.0088, 0.0078, 0.0007, 0.0113, 0.0047])
    result = compare_daily_returns("trending", trending, "noise", flat_noise)
    assert result.autocorrelation_warning_a is True


def test_autocorrelation_warning_false_for_unpatterned_noise():
    # A fixed, non-trending series (generated once with a seeded RNG) --
    # Ljung-Box should not find significant autocorrelation in it.
    noise = pd.Series([0.0030, -0.0104, 0.0075, 0.0094, -0.0195, -0.0130, 0.0013, -0.0032, -0.0002, -0.0085, 0.0088, 0.0078, 0.0007, 0.0113, 0.0047])
    trending = pd.Series([0.001 * i for i in range(1, 16)])
    result = compare_daily_returns("noise", noise, "trending", trending)
    assert result.autocorrelation_warning_a is False


# ---------------------------------------------------------------------------
# Task #23: multiple-comparison correction
# ---------------------------------------------------------------------------


def test_adjust_for_multiple_comparisons_benjamini_hochberg_orders_correctly():
    a = pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5)
    b = pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5)
    c = pd.Series([0.051, 0.049, 0.050, 0.0495, 0.0505] * 5)  # similar spread to a, close mean -> weak/no diff
    results = [
        compare_daily_returns("a", a, "b", b),
        compare_daily_returns("a", a, "c", c),
    ]
    adjusted = adjust_for_multiple_comparisons(results, method="benjamini-hochberg")
    by_pair = {(r.model_a, r.model_b): r for r in adjusted}
    assert by_pair[("a", "b")].p_value_adjusted is not None
    assert by_pair[("a", "c")].p_value_adjusted is not None
    # BH-adjusted p-values are never smaller than the raw p-value.
    assert by_pair[("a", "b")].p_value_adjusted >= by_pair[("a", "b")].p_value
    assert by_pair[("a", "c")].p_value_adjusted >= by_pair[("a", "c")].p_value


def test_adjust_for_multiple_comparisons_bonferroni_multiplies_by_count():
    a = pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5)
    b = pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5)
    c = pd.Series([0.049, 0.051, 0.050, 0.0505, 0.0495] * 5)
    d = pd.Series([-0.049, -0.051, -0.050, -0.0505, -0.0495] * 5)
    results = [
        compare_daily_returns("a", a, "b", b),
        compare_daily_returns("c", c, "d", d),
    ]
    adjusted = adjust_for_multiple_comparisons(results, method="bonferroni")
    for raw, corrected in zip(results, adjusted):
        expected = min(raw.p_value * len(results), 1.0)
        assert corrected.p_value_adjusted == pytest.approx(expected)


def test_adjust_for_multiple_comparisons_leaves_none_pvalues_as_none():
    zero_var = pd.Series([0.0, 0.0, 0.0])
    other = pd.Series([0.01, 0.02, 0.03])
    undefined = compare_daily_returns("flat_a", zero_var, "flat_b", zero_var)
    defined = compare_daily_returns("x", other, "y", pd.Series([-0.01, -0.02, -0.03] * 2 + [0.0]))
    adjusted = adjust_for_multiple_comparisons([undefined, defined])
    by_pair = {(r.model_a, r.model_b): r for r in adjusted}
    assert by_pair[("flat_a", "flat_b")].p_value_adjusted is None


def test_adjust_for_multiple_comparisons_rejects_unknown_method():
    a = pd.Series([0.01, 0.02, 0.03])
    b = pd.Series([-0.01, -0.02, -0.03])
    results = [compare_daily_returns("a", a, "b", b)]
    with pytest.raises(ValueError):
        adjust_for_multiple_comparisons(results, method="not-a-real-method")


def test_compare_all_pairs_applies_correction_by_default():
    returns_by_model = {
        "a": pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5),
        "b": pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5),
        "c": pd.Series([0.049, 0.051, 0.050, 0.0505, 0.0495] * 5),
    }
    results = compare_all_pairs(returns_by_model)
    assert len(results) == 3
    assert all(r.p_value_adjusted is not None for r in results)


def test_compare_all_pairs_correction_method_none_skips_adjustment():
    returns_by_model = {
        "a": pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5),
        "b": pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5),
    }
    results = compare_all_pairs(returns_by_model, correction_method=None)
    assert all(r.p_value_adjusted is None for r in results)


# ---------------------------------------------------------------------------
# Follow-up (2026-09-14): block-bootstrap p-value, robust to autocorrelation
# ---------------------------------------------------------------------------


def test_block_bootstrap_pvalue_none_with_too_few_observations():
    # Below 4 observations there isn't enough data for a block of any
    # meaningful length -- None, not a guess.
    a = np.array([0.1, 0.2, 0.3])
    b = np.array([0.1, 0.2, 0.3, 0.4])
    assert block_bootstrap_pvalue(a, b) is None


def test_block_bootstrap_pvalue_low_for_clearly_different_means():
    rng = np.random.default_rng(1)
    a = 0.05 + rng.normal(0, 0.005, size=30)
    b = -0.05 + rng.normal(0, 0.005, size=30)
    p = block_bootstrap_pvalue(a, b, n_resamples=500, random_state=123)
    assert p is not None
    assert p < 0.05


def test_block_bootstrap_pvalue_high_when_no_true_difference():
    # Two independent draws from the *same* distribution -- the block
    # bootstrap should not manufacture a spuriously low p-value.
    a = np.random.default_rng(2).normal(0, 0.01, size=30)
    b = np.random.default_rng(3).normal(0, 0.01, size=30)
    p = block_bootstrap_pvalue(a, b, n_resamples=500, random_state=123)
    assert p is not None
    assert p > 0.05


def test_block_bootstrap_pvalue_reproducible_with_fixed_seed():
    a = 0.05 + np.random.default_rng(1).normal(0, 0.005, size=30)
    b = -0.05 + np.random.default_rng(4).normal(0, 0.005, size=30)
    p1 = block_bootstrap_pvalue(a, b, n_resamples=300, random_state=7)
    p2 = block_bootstrap_pvalue(a, b, n_resamples=300, random_state=7)
    assert p1 == p2


def test_block_bootstrap_pvalue_is_one_for_identical_zero_variance_series():
    # Both series flat at 0.0 (no trades yet): observed difference is
    # exactly 0, and every resample under the null is also exactly 0 --
    # the bootstrap correctly reports "no evidence of a difference"
    # (p=1.0) rather than raising or returning NaN.
    z = np.zeros(6)
    assert block_bootstrap_pvalue(z, z, n_resamples=200, random_state=1) == 1.0


def test_compare_daily_returns_includes_reproducible_block_bootstrap_p_value():
    a = pd.Series(0.05 + np.random.default_rng(1).normal(0, 0.005, size=30))
    b = pd.Series(-0.05 + np.random.default_rng(4).normal(0, 0.005, size=30))
    r1 = compare_daily_returns("a", a, "b", b, bootstrap_resamples=300)
    r2 = compare_daily_returns("a", a, "b", b, bootstrap_resamples=300)
    assert r1.p_value_block_bootstrap is not None
    assert r1.p_value_block_bootstrap < 0.05
    # bootstrap_random_state defaults to a fixed seed, so re-running the
    # comparison against the same data (e.g. a manual pipeline re-run)
    # must reproduce the same p-value rather than a fresh random one.
    assert r1.p_value_block_bootstrap == r2.p_value_block_bootstrap


def test_compare_daily_returns_p_value_block_bootstrap_none_for_short_series():
    a = pd.Series([0.01, 0.02])
    b = pd.Series([0.03, 0.04])
    result = compare_daily_returns("a", a, "b", b)
    assert result.p_value_block_bootstrap is None


def test_adjust_for_multiple_comparisons_leaves_block_bootstrap_p_value_untouched():
    a = pd.Series([0.05, 0.06, 0.055, 0.052, 0.058] * 5)
    b = pd.Series([-0.05, -0.06, -0.055, -0.052, -0.058] * 5)
    result = compare_daily_returns("a", a, "b", b)
    (adjusted,) = adjust_for_multiple_comparisons([result])
    assert adjusted.p_value_block_bootstrap == result.p_value_block_bootstrap

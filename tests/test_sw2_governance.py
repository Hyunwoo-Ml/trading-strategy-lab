import pandas as pd
import pytest

from sw2.compare import ComparisonResult
from sw2.governance import consecutive_advantage_days, evaluate_promotion


def make_comparison(**overrides) -> ComparisonResult:
    defaults = dict(
        model_a="a",
        model_b="b",
        mean_daily_return_a=0.01,
        mean_daily_return_b=-0.005,
        t_statistic=5.0,
        p_value=0.001,
        n_a=25,
        n_b=25,
        cohens_d=1.2,
        p_value_adjusted=0.005,
        autocorrelation_warning_a=False,
        autocorrelation_warning_b=False,
    )
    defaults.update(overrides)
    return ComparisonResult(**defaults)


# ---------------------------------------------------------------------------
# evaluate_promotion
# ---------------------------------------------------------------------------


def test_insufficient_data_when_either_n_below_minimum():
    comparison = make_comparison(n_a=10, n_b=25)
    verdict = evaluate_promotion(comparison, min_n=20)
    assert verdict.verdict == "insufficient_data"
    assert not verdict.promotable
    assert any("sample size too small" in r for r in verdict.reasons)


def test_hold_when_not_significant():
    comparison = make_comparison(p_value_adjusted=0.20, p_value=0.20)
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "hold"
    assert any("not statistically significant" in r for r in verdict.reasons)


def test_hold_when_p_value_undefined():
    comparison = make_comparison(p_value=None, p_value_adjusted=None, t_statistic=None, cohens_d=None)
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "hold"
    assert any("undefined" in r for r in verdict.reasons)


def test_hold_when_model_a_does_not_lead_on_mean_return():
    comparison = make_comparison(mean_daily_return_a=-0.01, mean_daily_return_b=0.02)
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "hold"
    assert any("does not exceed" in r for r in verdict.reasons)


def test_promote_when_all_criteria_met_without_streak_data():
    comparison = make_comparison()
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "promote"
    assert verdict.promotable
    assert verdict.consecutive_advantage_days_a is None


def test_uses_corrected_p_value_over_raw_when_both_present():
    # raw p is significant but the corrected one isn't -- correction should win.
    comparison = make_comparison(p_value=0.001, p_value_adjusted=0.5)
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "hold"


def test_falls_back_to_raw_p_value_with_caveat_when_uncorrected():
    comparison = make_comparison(p_value=0.001, p_value_adjusted=None)
    verdict = evaluate_promotion(comparison)
    assert verdict.verdict == "promote"
    assert any("uncorrected" in r for r in verdict.reasons)


def test_autocorrelation_warning_surfaces_as_a_caveat_reason():
    comparison = make_comparison(autocorrelation_warning_a=True)
    verdict = evaluate_promotion(comparison)
    # still promotable -- it's a caveat, not a disqualifier -- but must be recorded.
    assert verdict.verdict == "promote"
    assert any("autocorrelation" in r for r in verdict.reasons)


def test_hold_when_advantage_streak_too_short():
    comparison = make_comparison()
    # a leads in the aggregate mean, but its most recent day was a loss --
    # streak resets to 0, below the default minimum of 5.
    returns_a = pd.Series([0.01] * 24 + [-0.02])
    returns_b = pd.Series([-0.01] * 24 + [0.03])
    verdict = evaluate_promotion(comparison, returns_a, returns_b)
    assert verdict.verdict == "hold"
    assert verdict.consecutive_advantage_days_a == 0
    assert any("advantage streak too short" in r for r in verdict.reasons)


def test_promote_when_advantage_streak_long_enough():
    comparison = make_comparison()
    returns_a = pd.Series([0.01] * 25)
    returns_b = pd.Series([-0.01] * 25)
    verdict = evaluate_promotion(comparison, returns_a, returns_b, min_consecutive_advantage_days=5)
    assert verdict.verdict == "promote"
    assert verdict.consecutive_advantage_days_a == 25


def test_custom_thresholds_are_respected():
    comparison = make_comparison(n_a=15, n_b=15)
    # would be insufficient_data at the default min_n=20, but not at min_n=10
    verdict = evaluate_promotion(comparison, min_n=10)
    assert verdict.verdict == "promote"


# ---------------------------------------------------------------------------
# consecutive_advantage_days
# ---------------------------------------------------------------------------


def test_consecutive_advantage_days_counts_trailing_wins():
    a = pd.Series([-0.01, 0.01, 0.02, 0.03])
    b = pd.Series([0.02, -0.01, -0.01, -0.01])
    # last 3 days: a > b, a > b, a > b -- first day: a < b (breaks the streak before it starts counting back further)
    assert consecutive_advantage_days(a, b) == 3


def test_consecutive_advantage_days_resets_on_most_recent_loss():
    a = pd.Series([0.05, 0.05, 0.05, -0.01])
    b = pd.Series([-0.01, -0.01, -0.01, 0.02])
    assert consecutive_advantage_days(a, b) == 0


def test_consecutive_advantage_days_stops_at_nan():
    a = pd.Series([None, 0.01, 0.02])
    b = pd.Series([None, -0.01, -0.01])
    # only 2 valid trailing days before hitting NaN
    assert consecutive_advantage_days(a, b) == 2


def test_consecutive_advantage_days_uses_shorter_series_length():
    a = pd.Series([0.01, 0.02, 0.03])
    b = pd.Series([-0.01, -0.01])  # shorter -- compared positionally from the end
    assert consecutive_advantage_days(a, b) == 2


def test_consecutive_advantage_days_zero_when_no_data():
    assert consecutive_advantage_days(pd.Series([], dtype=float), pd.Series([], dtype=float)) == 0

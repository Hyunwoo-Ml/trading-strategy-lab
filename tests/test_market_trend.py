from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sw1.market.trend import compute_trend_position, compute_trend_strength, label_for


def series(values):
    return pd.Series(values, index=pd.bdate_range("2025-01-01", periods=len(values)), dtype=float)


def steady(n, daily_pct, start=100.0):
    return series(start * (1 + daily_pct / 100) ** np.arange(n))


def test_steady_uptrend_scores_the_maximum():
    t = compute_trend_strength(steady(300, 0.7))
    assert t.components == {"short": 3, "mid": 3, "long": 3, "stack": 3, "momentum": 3}
    assert t.score == 15 and t.label == "very_strong"


def test_steady_downtrend_scores_the_minimum():
    t = compute_trend_strength(steady(300, -0.7))
    assert t.score == -15 and t.label == "very_weak"


def test_flat_series_is_neutral_zero():
    t = compute_trend_strength(series([100.0] * 300))
    assert t.score == 0 and t.label == "neutral"


def test_short_history_counts_missing_components_as_zero():
    t = compute_trend_strength(steady(30, 0.5))  # has MA20 and a 20-day return, nothing longer
    assert t.components["long"] == 0 and t.components["stack"] == 0 and t.components["mid"] == 0
    assert t.components["short"] > 0 and t.components["momentum"] > 0


def test_pullback_inside_uptrend_mixes_signs():
    values = list(steady(280, 0.4)) + list(np.linspace(1, 0.9, 20) * steady(280, 0.4).iloc[-1])
    t = compute_trend_strength(series(values))
    assert t.components["stack"] >= 1  # long-term structure still up
    assert t.components["short"] < 0  # but price is under the 20-day MA
    assert -15 < t.score < 15


@pytest.mark.parametrize(
    "score,label",
    [(15, "very_strong"), (11, "very_strong"), (10, "strong"), (6, "strong"), (5, "neutral"),
     (-5, "neutral"), (-6, "weak"), (-10, "weak"), (-11, "very_weak"), (-15, "very_weak")],
)
def test_label_boundaries(score, label):
    assert label_for(score) == label


def test_trend_position_numbers():
    closes = series([100.0] * 250 + [110.0, 120.0, 108.0])
    p = compute_trend_position(closes)
    assert p.high_52w == 120.0 and p.low_52w == 100.0
    assert p.pct_from_high == pytest.approx(-10.0)
    assert p.return_1d == pytest.approx(-10.0)
    assert p.golden_stack is not None


def test_empty_series_raises():
    with pytest.raises(ValueError):
        compute_trend_strength(series([]))

import pandas as pd
import pytest

from sw1.scoring.integrate import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    ScoringWeights,
    TradeThresholds,
    classify_signal,
    compute_integrated_score,
    compute_quant_score,
)


def make_indicator_row(**overrides) -> pd.Series:
    base = {
        "RSI": 50.0,
        "MACD_HIST": 0.0,
        "BB_PCT_B": 0.5,
        "MA_CROSS": None,
        "VOL_RATIO": 1.0,
    }
    base.update(overrides)
    return pd.Series(base)


def test_weights_validate_accepts_defaults():
    DEFAULT_WEIGHTS.validate()


def test_weights_validate_rejects_bad_technical_sum():
    bad = ScoringWeights(rsi=0.5, macd=0.5, bollinger=0.5, ma_cross=0.5)
    with pytest.raises(ValueError):
        bad.validate()


def test_weights_validate_rejects_bad_blend_sum():
    bad = ScoringWeights(quant_blend=0.5, news_blend=0.6)
    with pytest.raises(ValueError):
        bad.validate()


def test_weights_validate_rejects_bad_dominance_sum():
    bad = ScoringWeights(consensus_weight=0.5, dominance_weight=0.6)
    with pytest.raises(ValueError):
        bad.validate()


def test_neutral_row_scores_near_zero():
    row = make_indicator_row()
    score = compute_quant_score(row)
    assert abs(score) < 0.05


def test_oversold_rsi_pushes_score_positive():
    row = make_indicator_row(RSI=20.0)
    score = compute_quant_score(row)
    assert score > 0


def test_overbought_rsi_pushes_score_negative():
    row = make_indicator_row(RSI=85.0)
    score = compute_quant_score(row)
    assert score < 0


def test_golden_cross_pushes_score_positive():
    neutral = compute_quant_score(make_indicator_row())
    golden = compute_quant_score(make_indicator_row(MA_CROSS="golden"))
    assert golden > neutral


def test_dead_cross_pushes_score_negative():
    neutral = compute_quant_score(make_indicator_row())
    dead = compute_quant_score(make_indicator_row(MA_CROSS="dead"))
    assert dead < neutral


def test_quant_score_bounded():
    row = make_indicator_row(RSI=0.0, MACD_HIST=100.0, BB_PCT_B=-5.0, MA_CROSS="golden", VOL_RATIO=3.0)
    score = compute_quant_score(row)
    assert -1.0 <= score <= 1.0


def test_integrated_score_rejects_out_of_range_inputs():
    with pytest.raises(ValueError):
        compute_integrated_score(1.5, 0.0)
    with pytest.raises(ValueError):
        compute_integrated_score(0.0, -1.5)


def test_integrated_score_blends_by_weight():
    weights = ScoringWeights(quant_blend=0.6, news_blend=0.4)
    score = compute_integrated_score(quant_score=1.0, news_score=-1.0, weights=weights)
    assert score == pytest.approx(0.6 - 0.4)


def test_classify_signal_buy_1():
    result = classify_signal(0.35)
    assert result.signal == "buy_1"


def test_classify_signal_buy_2():
    result = classify_signal(0.75)
    assert result.signal == "buy_2"


def test_classify_signal_hold_in_neutral_band():
    result = classify_signal(0.0)
    assert result.signal == "hold"


def test_classify_signal_caution():
    result = classify_signal(-0.5)
    assert result.signal == "caution"


def test_classify_signal_stop_loss_overrides_score():
    result = classify_signal(0.9, price_return_from_entry=-0.10)
    assert result.signal == "stop_loss"


def test_classify_signal_take_profit_overrides_score():
    result = classify_signal(0.0, price_return_from_entry=0.20)
    assert result.signal == "take_profit"


def test_default_thresholds_are_ordered_sensibly():
    t = DEFAULT_THRESHOLDS
    assert t.buy_1_score < t.buy_2_score
    assert t.stop_loss_pct < 0 < t.take_profit_pct


# -- 2026-09-22: 합의+지배 혼합 (consensus+dominance blend) --


def test_single_extreme_indicator_dominates_score():
    """A single maxed-out sub-score (RSI=0 -> _score_rsi=1.0, everything else
    neutral) used to be diluted to just its own weight share under pure
    consensus (0.25) -- not enough to clear buy_1_score (0.30) on its own.
    Under the consensus+dominance blend it should contribute far more."""
    row = make_indicator_row(RSI=0.0)
    score = compute_quant_score(row)
    pure_consensus_equivalent = 1.0 * DEFAULT_WEIGHTS.rsi  # what the old formula alone would give
    assert score > pure_consensus_equivalent
    assert score >= DEFAULT_THRESHOLDS.buy_1_score


def test_dominant_subscore_is_signed_max_abs():
    # RSI=40 -> mild +0.2; MA_CROSS=dead -> -1.0 (largest |value|) should dominate.
    row = make_indicator_row(RSI=40.0, MA_CROSS="dead")
    score = compute_quant_score(row)
    assert score == pytest.approx(-0.6)


def test_consensus_and_dominance_weights_are_tunable():
    row = make_indicator_row(RSI=0.0)
    pure_consensus = ScoringWeights(consensus_weight=1.0, dominance_weight=0.0)
    pure_dominance = ScoringWeights(consensus_weight=0.0, dominance_weight=1.0)
    consensus_only_score = compute_quant_score(row, weights=pure_consensus)
    dominance_only_score = compute_quant_score(row, weights=pure_dominance)
    assert consensus_only_score == pytest.approx(0.25)  # exactly the old weighted-average formula
    assert dominance_only_score == pytest.approx(1.0)   # the dominant sub-score alone, unweighted-average


# -- 2026-09-22: MACD scale normalized by per-ticker MACD_HIST_STD_60 --


def test_macd_scale_uses_per_ticker_std_when_present():
    row_default_scale = make_indicator_row(MACD_HIST=2.0)
    row_tighter_scale = make_indicator_row(MACD_HIST=2.0, MACD_HIST_STD_60=0.5)
    # a smaller scale means the same histogram value saturates the tanh more
    assert compute_quant_score(row_tighter_scale) > compute_quant_score(row_default_scale)


def test_macd_scale_falls_back_to_one_when_std_missing_or_nan():
    row_missing = make_indicator_row(MACD_HIST=2.0)
    row_nan = make_indicator_row(MACD_HIST=2.0, MACD_HIST_STD_60=float("nan"))
    assert compute_quant_score(row_nan) == pytest.approx(compute_quant_score(row_missing))


def test_macd_scale_falls_back_to_one_when_std_is_zero_or_negative():
    row_missing = make_indicator_row(MACD_HIST=2.0)
    row_zero = make_indicator_row(MACD_HIST=2.0, MACD_HIST_STD_60=0.0)
    row_negative = make_indicator_row(MACD_HIST=2.0, MACD_HIST_STD_60=-0.5)
    assert compute_quant_score(row_zero) == pytest.approx(compute_quant_score(row_missing))
    assert compute_quant_score(row_negative) == pytest.approx(compute_quant_score(row_missing))


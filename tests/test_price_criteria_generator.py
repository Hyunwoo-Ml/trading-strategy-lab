from __future__ import annotations

import json

import pandas as pd
import pytest

from sw1.criteria.generator import (
    PriceCriteriaParams,
    generate_price_criteria,
    load_price_criteria_params,
)
from sw1.indicators.technical import compute_all_technical_indicators

from .conftest import make_mock_ohlcv


@pytest.fixture
def indicators_df() -> pd.DataFrame:
    return compute_all_technical_indicators(make_mock_ohlcv(n=300))


def test_params_validate_rejects_positive_stop_loss():
    params = PriceCriteriaParams(model_name="bad", stop_loss_pct=0.05)
    with pytest.raises(ValueError, match="stop_loss_pct"):
        params.validate()


def test_params_validate_rejects_nonpositive_reward_risk_ratio():
    params = PriceCriteriaParams(model_name="bad", reward_risk_ratio=0)
    with pytest.raises(ValueError, match="reward_risk_ratio"):
        params.validate()


def test_generate_price_criteria_orders_levels_sanely(indicators_df):
    params = PriceCriteriaParams(model_name="model_1", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    criteria = generate_price_criteria("AAPL", indicators_df, params)

    # buy_2 (deeper pullback entry) must be at or below buy_1 (shallower entry)
    assert criteria.buy_2_price <= criteria.buy_1_price
    # target should sit above buy_1 -- it's a take-profit level
    assert criteria.target_price > criteria.buy_1_price
    assert criteria.ticker == "AAPL"
    assert criteria.model_name == "model_1"
    assert criteria.stop_loss_pct == -0.08
    assert "buy_1" in criteria.basis and "buy_2" in criteria.basis and "target" in criteria.basis


def test_target_price_scales_with_reward_risk_ratio(indicators_df):
    low_r = generate_price_criteria("AAPL", indicators_df, PriceCriteriaParams(model_name="m", reward_risk_ratio=1.0))
    high_r = generate_price_criteria("AAPL", indicators_df, PriceCriteriaParams(model_name="m", reward_risk_ratio=3.0))
    assert high_r.target_price > low_r.target_price


def test_tighter_stop_loss_pulls_target_closer(indicators_df):
    tight = generate_price_criteria("AAPL", indicators_df, PriceCriteriaParams(model_name="m", stop_loss_pct=-0.03))
    loose = generate_price_criteria("AAPL", indicators_df, PriceCriteriaParams(model_name="m", stop_loss_pct=-0.15))
    # smaller risk-per-share (tighter stop) -> smaller target distance, same R multiple
    assert tight.target_price < loose.target_price


def test_generate_price_criteria_falls_back_with_short_history():
    short_df = compute_all_technical_indicators(make_mock_ohlcv(n=10))
    params = PriceCriteriaParams(model_name="model_1")
    criteria = generate_price_criteria("AAPL", short_df, params)
    # BB_LOWER/MA_50 are all NaN with only 10 rows -- must fall back, not crash.
    # 2026-09-22 (1-day lag): the fallback (reference_close * 0.97) is anchored
    # to YESTERDAY's close, not to criteria.close (today's) -- so the only
    # invariant guaranteed by construction is against the reference close.
    reference_close = float(short_df["Close"].iloc[-2])
    assert criteria.buy_1_price < reference_close
    assert criteria.buy_2_price <= criteria.buy_1_price
    assert "폴백" in criteria.basis["buy_1"]
    assert "전일 종가 기준" in criteria.basis["buy_1"]


def test_generate_price_criteria_anchors_support_levels_to_reference_day(indicators_df):
    """The 1-day-lag fix (2026-09-22): buy_1_price/buy_2_price must come from
    data through YESTERDAY (iloc[-2]), not from today's own row -- otherwise
    buy_1_price is mathematically always below today's close and a
    `close <= buy_1_price` entry check can (almost) never fire. Regenerating
    criteria from indicators_df with today's row perturbed should NOT change
    buy_1_price/buy_2_price/target_price at all, since none of them should
    read today's row."""
    params = PriceCriteriaParams(model_name="model_1", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    baseline = generate_price_criteria("AAPL", indicators_df, params)

    perturbed_df = indicators_df.copy()
    perturbed_df.iloc[-1, perturbed_df.columns.get_loc("Close")] = float(indicators_df["Close"].iloc[-1]) * 1.5
    perturbed = generate_price_criteria("AAPL", perturbed_df, params)

    assert perturbed.buy_1_price == baseline.buy_1_price
    assert perturbed.buy_2_price == baseline.buy_2_price
    assert perturbed.target_price == baseline.target_price
    # ...but criteria.close itself DOES track today's (perturbed) row, since
    # that's the value actually compared against buy_1_price/buy_2_price.
    assert perturbed.close == pytest.approx(float(indicators_df["Close"].iloc[-1]) * 1.5)


def test_generate_price_criteria_single_row_falls_back_to_same_day():
    """With < 2 rows there is no "yesterday" to anchor to -- must degrade to
    the old same-day behavior instead of crashing (e.g. iloc[-2])."""
    one_row_df = compute_all_technical_indicators(make_mock_ohlcv(n=1))
    params = PriceCriteriaParams(model_name="model_1")
    criteria = generate_price_criteria("AAPL", one_row_df, params)
    assert criteria.buy_1_price < criteria.close
    assert "전일 종가 기준" not in criteria.basis["buy_1"]


def test_generate_price_criteria_rejects_empty_df():
    params = PriceCriteriaParams(model_name="model_1")
    with pytest.raises(ValueError, match="empty"):
        generate_price_criteria("AAPL", pd.DataFrame(), params)


def test_load_price_criteria_params_reads_json_files(tmp_path):
    config_dir = tmp_path / "price_criteria_models"
    config_dir.mkdir()
    (config_dir / "model_1.json").write_text(
        json.dumps({"model_name": "model_1", "stop_loss_pct": -0.08, "reward_risk_ratio": 2.0, "buy2_lookback_days": 60}),
        encoding="utf-8",
    )
    (config_dir / "model_2.json").write_text(
        json.dumps({"model_name": "model_2", "stop_loss_pct": -0.05, "reward_risk_ratio": 3.0}),
        encoding="utf-8",
    )

    params_list = load_price_criteria_params(config_dir)
    names = {p.model_name for p in params_list}
    assert names == {"model_1", "model_2"}


def test_load_price_criteria_params_missing_dir_returns_empty(tmp_path):
    assert load_price_criteria_params(tmp_path / "does_not_exist") == []


def test_load_price_criteria_params_validates_each_file(tmp_path):
    config_dir = tmp_path / "price_criteria_models"
    config_dir.mkdir()
    (config_dir / "bad.json").write_text(
        json.dumps({"model_name": "bad", "stop_loss_pct": 0.05}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="stop_loss_pct"):
        load_price_criteria_params(config_dir)


# -- 2026-09-13 feedback: volume/weekly confirmation params --


def test_min_volume_ratio_defaults_to_one():
    params = PriceCriteriaParams(model_name="m")
    assert params.min_volume_ratio == 1.0
    assert params.require_weekly_uptrend is False


def test_negative_min_volume_ratio_rejected():
    params = PriceCriteriaParams(model_name="bad", min_volume_ratio=-0.5)
    with pytest.raises(ValueError, match="min_volume_ratio"):
        params.validate()


def test_load_price_criteria_params_reads_new_confirmation_fields(tmp_path):
    config_dir = tmp_path / "price_criteria_models"
    config_dir.mkdir()
    (config_dir / "model_1.json").write_text(
        json.dumps(
            {
                "model_name": "model_1",
                "min_volume_ratio": 1.3,
                "require_weekly_uptrend": True,
            }
        ),
        encoding="utf-8",
    )
    [params] = load_price_criteria_params(config_dir)
    assert params.min_volume_ratio == 1.3
    assert params.require_weekly_uptrend is True


# -- 2026-09-14 feedback: 가격기준모델에도 뉴스/시황 데이터 반영 --


def test_min_news_score_defaults_to_none():
    params = PriceCriteriaParams(model_name="m")
    assert params.min_news_score is None


def test_min_news_score_out_of_range_rejected():
    params = PriceCriteriaParams(model_name="bad", min_news_score=-1.5)
    with pytest.raises(ValueError, match="min_news_score"):
        params.validate()

    params = PriceCriteriaParams(model_name="bad", min_news_score=1.5)
    with pytest.raises(ValueError, match="min_news_score"):
        params.validate()


def test_min_news_score_in_range_is_accepted():
    params = PriceCriteriaParams(model_name="ok", min_news_score=-0.3)
    params.validate()  # should not raise


def test_load_price_criteria_params_reads_min_news_score(tmp_path):
    config_dir = tmp_path / "price_criteria_models"
    config_dir.mkdir()
    (config_dir / "model_1.json").write_text(
        json.dumps({"model_name": "model_1", "min_news_score": -0.35}),
        encoding="utf-8",
    )
    [params] = load_price_criteria_params(config_dir)
    assert params.min_news_score == -0.35


def test_load_price_criteria_params_min_news_score_defaults_to_none_when_absent(tmp_path):
    config_dir = tmp_path / "price_criteria_models"
    config_dir.mkdir()
    (config_dir / "model_1.json").write_text(
        json.dumps({"model_name": "model_1"}),
        encoding="utf-8",
    )
    [params] = load_price_criteria_params(config_dir)
    assert params.min_news_score is None

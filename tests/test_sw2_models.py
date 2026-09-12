import pytest

from sw1.scoring.integrate import ScoringWeights
from sw2.models import ModelRegistry, TradingModel, default_registry


def test_default_registry_has_expected_models():
    registry = default_registry()
    assert set(registry.names()) == {"baseline", "technical_only", "conservative"}


def test_registry_rejects_duplicate_names():
    registry = ModelRegistry()
    registry.register(TradingModel(name="a"))
    with pytest.raises(ValueError):
        registry.register(TradingModel(name="a"))


def test_registry_get_unknown_raises_with_helpful_message():
    registry = default_registry()
    with pytest.raises(KeyError):
        registry.get("nonexistent")


def test_model_post_init_validates_weights():
    with pytest.raises(ValueError):
        TradingModel(name="bad", weights=ScoringWeights(rsi=0.9, macd=0.9, bollinger=0.9, ma_cross=0.9))


def test_technical_only_model_ignores_news_score():
    registry = default_registry()
    model = registry.get("technical_only")
    # same quant_score, wildly different news_score -- decision shouldn't change
    signal_bad_news = model.decide(quant_score=0.5, news_score=-1.0)
    signal_good_news = model.decide(quant_score=0.5, news_score=1.0)
    assert signal_bad_news.signal == signal_good_news.signal
    assert signal_bad_news.integrated_score == pytest.approx(signal_good_news.integrated_score)


def test_baseline_model_blends_news_into_decision():
    registry = default_registry()
    model = registry.get("baseline")
    signal_bad_news = model.decide(quant_score=0.5, news_score=-1.0)
    signal_good_news = model.decide(quant_score=0.5, news_score=1.0)
    assert signal_bad_news.integrated_score < signal_good_news.integrated_score


def test_conservative_model_has_higher_buy_threshold_than_baseline():
    registry = default_registry()
    baseline = registry.get("baseline")
    conservative = registry.get("conservative")
    assert conservative.thresholds.buy_1_score > baseline.thresholds.buy_1_score


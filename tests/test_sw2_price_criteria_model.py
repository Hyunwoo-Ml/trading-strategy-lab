from __future__ import annotations

from sw1.criteria.generator import PriceCriteria, PriceCriteriaParams
from sw2.price_criteria_model import PriceCriteriaModel


def make_criteria(**overrides) -> PriceCriteria:
    defaults = dict(
        model_name="model_1",
        ticker="AAPL",
        date="2026-09-13",
        close=200.0,
        buy_1_price=195.0,
        buy_2_price=180.0,
        stop_loss_pct=-0.08,
        target_price=225.0,
        basis={"buy_1": "test", "buy_2": "test", "target": "test"},
    )
    defaults.update(overrides)
    return PriceCriteria(**defaults)


def make_model(**overrides) -> PriceCriteriaModel:
    params = PriceCriteriaParams(model_name="model_1", **overrides)
    return PriceCriteriaModel(params=params)


def test_not_holding_and_price_above_buy1_holds():
    model = make_model()
    criteria = make_criteria(close=210.0, buy_1_price=195.0)
    signal = model.decide(criteria, is_holding=False, tranche_count=0, price_return_from_entry=None)
    assert signal.signal == "hold"


def test_not_holding_and_price_at_or_below_buy1_triggers_buy1():
    model = make_model()
    criteria = make_criteria(close=194.0, buy_1_price=195.0)
    signal = model.decide(criteria, is_holding=False, tranche_count=0, price_return_from_entry=None)
    assert signal.signal == "buy_1"


def test_holding_one_tranche_and_price_at_buy2_triggers_buy2():
    model = make_model()
    criteria = make_criteria(close=179.0, buy_1_price=195.0, buy_2_price=180.0)
    signal = model.decide(criteria, is_holding=True, tranche_count=1, price_return_from_entry=-0.02)
    assert signal.signal == "buy_2"


def test_holding_two_tranches_does_not_trigger_buy2_again():
    model = make_model()
    criteria = make_criteria(close=179.0, buy_1_price=195.0, buy_2_price=180.0)
    signal = model.decide(criteria, is_holding=True, tranche_count=2, price_return_from_entry=-0.02)
    assert signal.signal == "hold"


def test_holding_and_return_below_stop_loss_triggers_stop_loss():
    model = make_model(stop_loss_pct=-0.08)
    criteria = make_criteria(stop_loss_pct=-0.08)
    signal = model.decide(criteria, is_holding=True, tranche_count=1, price_return_from_entry=-0.10)
    assert signal.signal == "stop_loss"


def test_holding_and_close_at_or_above_target_triggers_take_profit():
    model = make_model()
    criteria = make_criteria(close=230.0, target_price=225.0)
    signal = model.decide(criteria, is_holding=True, tranche_count=1, price_return_from_entry=0.10)
    assert signal.signal == "take_profit"


def test_stop_loss_takes_priority_over_take_profit_if_both_somehow_true():
    # contrived, but stop-loss must win -- capital preservation first
    model = make_model()
    criteria = make_criteria(close=230.0, target_price=225.0, stop_loss_pct=-0.08)
    signal = model.decide(criteria, is_holding=True, tranche_count=1, price_return_from_entry=-0.09)
    assert signal.signal == "stop_loss"


def test_model_name_matches_params():
    model = make_model()
    assert model.name == "model_1"

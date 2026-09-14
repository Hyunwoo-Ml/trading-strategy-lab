import pytest

from sw2.ledger import Portfolio

# Most tests below use transaction_cost_pct=0.0 explicitly -- they're
# testing the core share/cash arithmetic and want round, unambiguous
# numbers. The fee behavior itself (transaction_cost_pct > 0) has its own
# dedicated test section further down.


def test_starts_with_full_cash_and_no_positions():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    assert p.cash == 100_000.0
    assert p.positions == {}


def test_default_transaction_cost_pct_is_nonzero():
    # transaction costs should be "on" by default for new portfolios --
    # the whole point of Task #24 is that fees aren't opt-in.
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    assert p.transaction_cost_pct == pytest.approx(0.001)


def test_buy_reduces_cash_and_opens_position():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=200.0, dollar_amount=10_000.0, action="buy_1")
    assert p.cash == pytest.approx(90_000.0)
    assert p.positions["AAPL"].shares == pytest.approx(50.0)
    assert p.positions["AAPL"].entry_price == pytest.approx(200.0)
    assert len(p.trades) == 1


def test_buy_twice_averages_entry_price():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    p.buy(date="2026-09-02", ticker="AAPL", price=200.0, dollar_amount=10_000.0, action="buy_2")
    # 100 shares @100 + 50 shares @200 -> 150 shares, avg = (10000+10000)/150
    assert p.positions["AAPL"].shares == pytest.approx(150.0)
    assert p.positions["AAPL"].entry_price == pytest.approx(20_000.0 / 150.0)


def test_buy_clips_to_available_cash():
    p = Portfolio(model_name="test", starting_cash=5_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    assert p.cash == pytest.approx(0.0)
    assert p.positions["AAPL"].shares == pytest.approx(50.0)


def test_sell_all_returns_cash_and_closes_position():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    p.sell_all(date="2026-09-05", ticker="AAPL", price=120.0, action="take_profit")
    assert "AAPL" not in p.positions
    assert p.cash == pytest.approx(90_000.0 + 100 * 120.0)


def test_sell_all_on_unheld_ticker_is_a_noop():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.sell_all(date="2026-09-05", ticker="AAPL", price=120.0, action="take_profit")
    assert p.cash == pytest.approx(100_000.0)
    assert p.trades == []


def test_mark_to_market_includes_open_positions():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    equity = p.mark_to_market(date="2026-09-02", prices={"AAPL": 110.0})
    assert equity == pytest.approx(90_000.0 + 100 * 110.0)
    assert p.equity_curve[-1]["equity"] == pytest.approx(equity)


def test_price_return_from_entry():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    assert p.price_return_from_entry("AAPL", 110.0) == pytest.approx(0.10)
    assert p.price_return_from_entry("MSFT", 500.0) is None


# -- transaction costs (Task #24) ---------------------------------------


def test_buy_charges_fee_and_reduces_shares_purchased():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.01)  # 1% for easy math
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    # cash still drops by the full dollar_amount (fee comes out of what
    # actually buys shares, not on top of the allocated tranche)
    assert p.cash == pytest.approx(90_000.0)
    # only 9,900 of the 10,000 actually buys shares -> 99 shares @ $100
    assert p.positions["AAPL"].shares == pytest.approx(99.0)
    assert p.trades[0].fee == pytest.approx(100.0)  # 1% of 10,000


def test_sell_all_charges_fee_and_reduces_proceeds():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.01)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    cash_after_buy = p.cash
    p.sell_all(date="2026-09-05", ticker="AAPL", price=100.0, action="take_profit")
    # 99 shares * $100 = 9,900 gross, minus 1% fee (99.0) = 9,801 net
    assert p.cash == pytest.approx(cash_after_buy + 9_801.0)
    assert p.trades[-1].fee == pytest.approx(99.0)


def test_zero_transaction_cost_pct_matches_pre_fee_behavior():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    assert p.positions["AAPL"].shares == pytest.approx(100.0)
    assert p.trades[0].fee == pytest.approx(0.0)


def test_higher_transaction_cost_leaves_less_equity_for_same_round_trip():
    # a full buy-then-sell round trip at the same price should lose money
    # exactly to the tune of the two fees -- this is the whole point of
    # modeling transaction costs at all.
    cheap = Portfolio(model_name="cheap", starting_cash=100_000.0, transaction_cost_pct=0.0)
    pricey = Portfolio(model_name="pricey", starting_cash=100_000.0, transaction_cost_pct=0.01)
    for p in (cheap, pricey):
        p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
        p.sell_all(date="2026-09-02", ticker="AAPL", price=100.0, action="take_profit")
    assert cheap.cash == pytest.approx(100_000.0)  # no fees, round trip at flat price is a wash
    assert pricey.cash < cheap.cash  # fees strictly cost money


def test_equity_df_computes_daily_return():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.mark_to_market(date="2026-09-01", prices={})
    p.mark_to_market(date="2026-09-02", prices={})
    p.cash = 110_000.0  # simulate a gain between marks
    p.mark_to_market(date="2026-09-03", prices={})
    df = p.equity_df()
    assert list(df["equity"]) == [100_000.0, 100_000.0, 110_000.0]
    assert df["daily_return"].iloc[-1] == pytest.approx(0.10)

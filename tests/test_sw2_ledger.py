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


# -- NaN price guards (2026-11-11 postmortem: a missing SW1 close for one
# ticker on one day used to be able to permanently break a model's whole
# equity curve -- see sw2/ledger.py's buy()/sell_all()/mark_to_market()) --


def test_buy_with_nan_price_is_a_noop():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=float("nan"), dollar_amount=10_000.0, action="buy_1")
    assert p.cash == pytest.approx(100_000.0)
    assert "AAPL" not in p.positions
    assert p.trades == []


def test_sell_all_with_nan_price_is_a_noop_and_keeps_position():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    cash_before = p.cash
    p.sell_all(date="2026-09-05", ticker="AAPL", price=float("nan"), action="take_profit")
    # a NaN-priced sell must not liquidate the position -- it should still
    # be there to sell properly once a real price comes back.
    assert "AAPL" in p.positions
    assert p.cash == pytest.approx(cash_before)
    assert len(p.trades) == 1  # only the original buy -- no bad sell recorded


def test_mark_to_market_falls_back_to_last_known_price_on_nan_gap():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    p.mark_to_market(date="2026-09-02", prices={"AAPL": 110.0})

    # 2026-09-03: provider gap -- AAPL missing from today's prices entirely
    equity_gap = p.mark_to_market(date="2026-09-03", prices={})
    assert equity_gap == pytest.approx(90_000.0 + 100 * 110.0)  # carried forward 110, not entry_price 100

    # 2026-09-04: still a gap, but this time the key is present and explicitly NaN
    equity_gap2 = p.mark_to_market(date="2026-09-04", prices={"AAPL": float("nan")})
    assert equity_gap2 == pytest.approx(90_000.0 + 100 * 110.0)

    # 2026-09-05: real price resumes
    equity_ok = p.mark_to_market(date="2026-09-05", prices={"AAPL": 120.0})
    assert equity_ok == pytest.approx(90_000.0 + 100 * 120.0)


def test_mark_to_market_falls_back_to_entry_price_when_never_seen_a_valid_price():
    p = Portfolio(model_name="test", starting_cash=100_000.0, transaction_cost_pct=0.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    # first-ever mark lands on a gap day -- no last_known_prices entry yet
    equity = p.mark_to_market(date="2026-09-02", prices={})
    assert equity == pytest.approx(90_000.0 + 100 * 100.0)


def test_equity_df_computes_daily_return():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.mark_to_market(date="2026-09-01", prices={})
    p.mark_to_market(date="2026-09-02", prices={})
    p.cash = 110_000.0  # simulate a gain between marks
    p.mark_to_market(date="2026-09-03", prices={})
    df = p.equity_df()
    assert list(df["equity"]) == [100_000.0, 100_000.0, 110_000.0]
    assert df["daily_return"].iloc[-1] == pytest.approx(0.10)


# -- 2026-09-25: duplicate / weekend equity points --------------------------


def test_mark_to_market_same_date_replaces_instead_of_appending():
    p = Portfolio(model_name="m")
    p.mark_to_market(date="2026-09-01", prices={})
    p.cash = 101_000.0
    p.mark_to_market(date="2026-09-01", prices={})
    assert len(p.equity_curve) == 1
    assert p.equity_curve[0]["equity"] == pytest.approx(101_000.0)


def test_equity_df_dedupes_dates_and_drops_weekends():
    p = Portfolio(model_name="m")
    # legacy persisted curve shape: repeated dates + a Sat/Sun point
    p.equity_curve = [
        {"date": "2026-09-12", "equity": 100_000.0},  # Sat
        {"date": "2026-09-13", "equity": 100_000.0},  # Sun
        {"date": "2026-09-14", "equity": 100_000.0},
        {"date": "2026-09-14", "equity": 100_000.0},
        {"date": "2026-09-14", "equity": 100_000.0},
        {"date": "2026-09-15", "equity": 101_000.0},
        {"date": "2026-09-15", "equity": 102_000.0},
    ]
    df = p.equity_df()
    assert [d.date().isoformat() for d in df.index] == ["2026-09-14", "2026-09-15"]
    assert df["equity"].iloc[-1] == pytest.approx(102_000.0)  # last point for a date wins
    assert df["daily_return"].iloc[-1] == pytest.approx(0.02)
    assert len(p.equity_curve) == 7  # persisted history itself untouched

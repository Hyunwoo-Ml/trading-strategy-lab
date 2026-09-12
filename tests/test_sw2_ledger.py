import pytest

from sw2.ledger import Portfolio


def test_starts_with_full_cash_and_no_positions():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    assert p.cash == 100_000.0
    assert p.positions == {}


def test_buy_reduces_cash_and_opens_position():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=200.0, dollar_amount=10_000.0, action="buy_1")
    assert p.cash == pytest.approx(90_000.0)
    assert p.positions["AAPL"].shares == pytest.approx(50.0)
    assert p.positions["AAPL"].entry_price == pytest.approx(200.0)
    assert len(p.trades) == 1


def test_buy_twice_averages_entry_price():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    p.buy(date="2026-09-02", ticker="AAPL", price=200.0, dollar_amount=10_000.0, action="buy_2")
    # 100 shares @100 + 50 shares @200 -> 150 shares, avg = (10000+10000)/150
    assert p.positions["AAPL"].shares == pytest.approx(150.0)
    assert p.positions["AAPL"].entry_price == pytest.approx(20_000.0 / 150.0)


def test_buy_clips_to_available_cash():
    p = Portfolio(model_name="test", starting_cash=5_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    assert p.cash == pytest.approx(0.0)
    assert p.positions["AAPL"].shares == pytest.approx(50.0)


def test_sell_all_returns_cash_and_closes_position():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    p.sell_all(date="2026-09-05", ticker="AAPL", price=120.0, action="take_profit")
    assert "AAPL" not in p.positions
    assert p.cash == pytest.approx(90_000.0 + 100 * 120.0)


def test_sell_all_on_unheld_ticker_is_a_noop():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.sell_all(date="2026-09-05", ticker="AAPL", price=120.0, action="take_profit")
    assert p.cash == pytest.approx(100_000.0)
    assert p.trades == []


def test_mark_to_market_includes_open_positions():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    equity = p.mark_to_market(date="2026-09-02", prices={"AAPL": 110.0})
    assert equity == pytest.approx(90_000.0 + 100 * 110.0)
    assert p.equity_curve[-1]["equity"] == pytest.approx(equity)


def test_price_return_from_entry():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.buy(date="2026-09-01", ticker="AAPL", price=100.0, dollar_amount=10_000.0, action="buy_1")
    assert p.price_return_from_entry("AAPL", 110.0) == pytest.approx(0.10)
    assert p.price_return_from_entry("MSFT", 500.0) is None


def test_equity_df_computes_daily_return():
    p = Portfolio(model_name="test", starting_cash=100_000.0)
    p.mark_to_market(date="2026-09-01", prices={})
    p.mark_to_market(date="2026-09-02", prices={})
    p.cash = 110_000.0  # simulate a gain between marks
    p.mark_to_market(date="2026-09-03", prices={})
    df = p.equity_df()
    assert list(df["equity"]) == [100_000.0, 100_000.0, 110_000.0]
    assert df["daily_return"].iloc[-1] == pytest.approx(0.10)


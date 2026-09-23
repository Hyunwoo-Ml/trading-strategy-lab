"""Tests for scripts/run_historical_backtest.py.

The underlying math (period bucketing) is unit-tested in tests/
test_backtest.py; the underlying technical-indicator/weekly-trend/market-
regime rules are unit-tested in their own sw1 test files
(test_weekly_trend.py, test_market_regime.py, test_price_criteria_
generator.py). What's tested HERE is this script's own wiring: does it
call the shared decision/ledger code correctly, does it degrade gracefully
on a bad ticker, does it write the expected output shape.

yfinance is mocked out entirely -- no network in tests, same pattern as
tests/test_run_walkforward_validation.py."""
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_historical_backtest as script  # noqa: E402


def _fake_ohlcv(n=300, seed=0, start="2023-01-02", vol=2.0):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range(start, periods=n)
    close = 100 + np.cumsum(rng.randn(n) * vol)
    close = np.maximum(close, 5.0)
    return pd.DataFrame(
        {
            "Open": close,
            "High": close * 1.01,
            "Low": close * 0.99,
            "Close": close,
            "Volume": rng.randint(1_000_000, 5_000_000, size=n),
        },
        index=dates,
    )


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    importlib.reload(script)
    output_path = tmp_path / "data" / "backtest" / "results.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", output_path)
    return script


def _mock_fetch(monkeypatch, mod, seed_offset=0):
    def fake_fetch(ticker, period="3y"):
        return _fake_ohlcv(seed=(abs(hash(ticker)) + seed_offset) % 1000)

    monkeypatch.setattr(mod, "fetch_ohlcv", fake_fetch)


# -- run_backtest / main wiring --


def test_main_writes_output_with_all_five_models(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script)
    rc = wired_script.main()
    assert rc == 0

    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert set(output["models"].keys()) == {
        "baseline", "technical_only", "conservative", "price_model_1", "price_model_2",
    }
    assert output["failures"] == []
    assert output["config"]["period_freq"] == "Q"
    assert output["config"]["fetch_period"] == "3y"
    assert len(output["config"]["scope_notes"]) > 0


def test_each_model_has_periods_and_summary_fields(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    for name, payload in output["models"].items():
        assert payload["starting_cash"] == wired_script.STARTING_CASH
        assert "total_return" in payload
        assert "final_equity" in payload
        assert "n_trades" in payload
        assert isinstance(payload["periods"], list)
        assert len(payload["periods"]) > 0
        for p in payload["periods"]:
            assert set(p.keys()) == {"period", "start_date", "end_date", "start_equity", "end_equity", "period_return"}


# -- max_drawdown wiring (2026-09-22 follow-up: quantify the RISK_FRACTION
# 2x sizing change's known trade-off -- bigger position sizes mean bigger
# equity swings on a stop-loss hit, not just bigger gains) --


def test_each_model_has_max_drawdown_with_expected_shape(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script, seed_offset=42)  # volatile enough to guarantee some decline somewhere
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    for name, payload in output["models"].items():
        assert "max_drawdown" in payload, name
        mdd = payload["max_drawdown"]
        assert set(mdd.keys()) == {"max_drawdown_pct", "peak_date", "trough_date", "peak_equity", "trough_equity"}
        # a real (even if empty-trade) equity curve is never truly empty here
        # -- mark_to_market runs every day regardless of trades -- so this
        # should never fall back to the all-None empty-curve case.
        assert mdd["max_drawdown_pct"] is not None
        assert mdd["max_drawdown_pct"] <= 0.0  # a decline from a peak is never positive


def test_each_benchmark_has_max_drawdown(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script, seed_offset=7)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    for name, payload in output["benchmarks"].items():
        assert "max_drawdown" in payload, name
        mdd = payload["max_drawdown"]
        assert set(mdd.keys()) == {"max_drawdown_pct", "peak_date", "trough_date", "peak_equity", "trough_equity"}
        assert mdd["max_drawdown_pct"] <= 0.0


def test_score_threshold_models_actually_trade_on_volatile_history(wired_script, monkeypatch):
    # sanity check that the score-threshold path (baseline/technical_only)
    # is genuinely wired to sw2.ledger.Portfolio, not just producing an
    # all-zero equity curve -- volatile synthetic history should clear at
    # least one model's buy_1 threshold somewhere across ~300 trading days.
    _mock_fetch(monkeypatch, wired_script, seed_offset=42)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    total_trades = sum(output["models"][m]["n_trades"] for m in ("baseline", "technical_only", "conservative"))
    assert total_trades > 0


def test_isolated_nan_close_does_not_crash_the_run(wired_script, monkeypatch):
    """2026-09-22 regression: real yfinance 3y history can carry a single
    NaN Close on an isolated day (data-provider gap). Before the 1-day-lag
    fix, price_model_1/2 never actually traded so a NaN close was never fed
    into Portfolio.buy()/mark_to_market(); once they started trading, an
    injected NaN like this one crashed the live workflow with
    `ValueError: Out of range float values are not JSON compliant: nan`
    from json.dumps(allow_nan=False). Reproduces that exact shape here with
    mocked data and asserts main() degrades gracefully instead."""
    def fetch_with_one_nan_close(ticker, period="3y"):
        df = _fake_ohlcv(seed=abs(hash(ticker)) % 1000, vol=6.0)
        df = df.copy()
        df.iloc[len(df) // 2, df.columns.get_loc("Close")] = float("nan")
        return df

    monkeypatch.setattr(wired_script, "fetch_ohlcv", fetch_with_one_nan_close)
    rc = wired_script.main()
    assert rc == 0

    raw = wired_script.OUTPUT_PATH.read_text(encoding="utf-8")
    assert "NaN" not in raw  # Python's json module spells a literal NaN this way if allow_nan slips through
    output = json.loads(raw)
    assert set(output["models"].keys()) == {
        "baseline", "technical_only", "conservative", "price_model_1", "price_model_2",
    }


def test_one_ticker_failure_does_not_crash_the_run(wired_script, monkeypatch):
    def flaky_fetch(ticker, period="3y"):
        if ticker == "TSLA":
            raise RuntimeError("simulated fetch failure")
        return _fake_ohlcv(seed=abs(hash(ticker)) % 1000)

    monkeypatch.setattr(wired_script, "fetch_ohlcv", flaky_fetch)
    rc = wired_script.main()
    assert rc == 0  # 6/7 tickers still came through

    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert any(t == "TSLA" for t, _err in output["failures"])
    assert len(output["models"]) == 5  # models still produced, just without TSLA's contribution


def test_all_tickers_failing_returns_nonzero(wired_script, monkeypatch):
    monkeypatch.setattr(
        wired_script, "fetch_ohlcv", lambda ticker, period="3y": (_ for _ in ()).throw(RuntimeError("boom"))
    )
    rc = wired_script.main()
    assert rc == 1
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert output["models"] == {}
    assert len(output["failures"]) == len(wired_script.M7_TICKERS)


def test_qqq_fetch_failure_degrades_gracefully_instead_of_crashing(wired_script, monkeypatch):
    def fetch_with_bad_qqq(ticker, period="3y"):
        if ticker == wired_script.MARKET_INDEX_TICKER:
            raise RuntimeError("simulated QQQ fetch failure")
        return _fake_ohlcv(seed=abs(hash(ticker)) % 1000)

    monkeypatch.setattr(wired_script, "fetch_ohlcv", fetch_with_bad_qqq)
    rc = wired_script.main()
    assert rc == 0
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert any(t == wired_script.MARKET_INDEX_TICKER for t, _err in output["failures"])
    assert len(output["models"]) == 5  # market-regime filter just defaults to risk_on everywhere, no crash


# -- _regime_series --


def test_regime_series_risk_off_when_confirmed_downtrend(wired_script):
    idx = pd.date_range("2024-01-01", periods=3)
    qqq = pd.DataFrame({"Close": [90.0, 100.0, 100.0], "MA_50": [95.0, 95.0, np.nan], "MA_200": [105.0, 105.0, 100.0]}, index=idx)
    result = wired_script._regime_series(qqq)
    assert result.iloc[0] == "risk_off"  # close < MA_50 < MA_200
    assert result.iloc[1] == "risk_on"   # close > MA_50
    assert result.iloc[2] == "risk_on"   # missing MA_50 -> defaults risk_on, same as compute_market_regime


# -- _apply_price_criteria_day (trade-application wiring, decoupled from generate_price_criteria) --


def test_apply_price_criteria_day_opens_position_on_buy_1_touch(wired_script):
    from sw2.ledger import Portfolio
    from sw2.price_criteria_model import MarketContext, PriceCriteriaModel
    from sw1.criteria.generator import PriceCriteriaParams

    params = PriceCriteriaParams(model_name="test_model", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    model = PriceCriteriaModel(params=params)
    portfolio = Portfolio(model_name="test_model", starting_cash=100_000.0)
    rows = [{"ticker": "AAPL", "close": 100.0, "buy_1_price": 105.0, "buy_2_price": 95.0, "stop_loss_pct": -0.08, "target_price": 120.0}]
    context = {"AAPL": MarketContext()}  # no confirmation gates active -> touch should convert straight to a buy

    wired_script._apply_price_criteria_day(model, portfolio, rows, "2024-01-02", context)

    assert "AAPL" in portfolio.positions
    assert len(portfolio.trades) == 1
    assert portfolio.trades[0].action == "buy_1"
    assert len(portfolio.equity_curve) == 1


def test_apply_price_criteria_day_blocked_entry_when_market_context_gates(wired_script):
    from sw2.ledger import Portfolio
    from sw2.price_criteria_model import MarketContext, PriceCriteriaModel
    from sw1.criteria.generator import PriceCriteriaParams

    params = PriceCriteriaParams(model_name="test_model", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    model = PriceCriteriaModel(params=params)
    portfolio = Portfolio(model_name="test_model", starting_cash=100_000.0)
    rows = [{"ticker": "AAPL", "close": 100.0, "buy_1_price": 105.0, "buy_2_price": 95.0, "stop_loss_pct": -0.08, "target_price": 120.0}]
    context = {"AAPL": MarketContext(market_regime="risk_off")}  # risk-off should hold back the new entry

    wired_script._apply_price_criteria_day(model, portfolio, rows, "2024-01-02", context)

    assert "AAPL" not in portfolio.positions
    assert portfolio.trades == []
    assert len(portfolio.equity_curve) == 1  # still marks to market even with no trade


def test_apply_price_criteria_day_stop_loss_closes_existing_position(wired_script):
    from sw2.ledger import Portfolio, Position
    from sw2.price_criteria_model import MarketContext, PriceCriteriaModel
    from sw1.criteria.generator import PriceCriteriaParams

    params = PriceCriteriaParams(model_name="test_model", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    model = PriceCriteriaModel(params=params)
    portfolio = Portfolio(model_name="test_model", starting_cash=100_000.0)
    portfolio.positions["AAPL"] = Position(ticker="AAPL", shares=10.0, entry_price=100.0, entry_date="2024-01-01")
    rows = [{"ticker": "AAPL", "close": 90.0, "buy_1_price": 85.0, "buy_2_price": 80.0, "stop_loss_pct": -0.08, "target_price": 130.0}]

    wired_script._apply_price_criteria_day(model, portfolio, rows, "2024-01-05", {"AAPL": MarketContext()})

    assert "AAPL" not in portfolio.positions
    assert portfolio.trades[-1].action == "stop_loss"


# -- benchmark buy-and-hold references (SPY / QQQ / TLT, 2026-09-22 user request) --


def test_benchmarks_present_with_expected_tickers(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert set(output["benchmarks"].keys()) == {"SPY", "QQQ", "TLT"}
    for key, payload in output["benchmarks"].items():
        assert payload["starting_cash"] == wired_script.STARTING_CASH
        assert payload["n_trades"] == 1  # pure buy-and-hold: one purchase, never sold
        assert isinstance(payload["periods"], list)
        assert len(payload["periods"]) > 0
        assert payload["label"]  # human-readable label present for the dashboard


def test_benchmark_total_return_matches_simple_buy_and_hold_math(wired_script, monkeypatch):
    _mock_fetch(monkeypatch, wired_script)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))

    # same seeded synthetic series the mock handed the script for "SPY"
    fake_spy = wired_script.fetch_ohlcv("SPY", period="3y")
    close = fake_spy["Close"].dropna()
    expected_return = close.iloc[-1] / close.iloc[0] - 1.0
    expected_equity = wired_script.STARTING_CASH * close.iloc[-1] / close.iloc[0]

    assert output["benchmarks"]["SPY"]["total_return"] == pytest.approx(expected_return, rel=1e-6)
    assert output["benchmarks"]["SPY"]["final_equity"] == pytest.approx(expected_equity, rel=1e-6)


def test_qqq_benchmark_reuses_regime_fetch_not_double_fetched(wired_script, monkeypatch):
    calls: list[str] = []

    def counting_fetch(ticker, period="3y"):
        calls.append(ticker)
        return _fake_ohlcv(seed=abs(hash(ticker)) % 1000)

    monkeypatch.setattr(wired_script, "fetch_ohlcv", counting_fetch)
    wired_script.main()
    assert calls.count("QQQ") == 1  # regime fetch is reused for the QQQ benchmark, not fetched a second time


def test_one_benchmark_failure_does_not_crash_the_run(wired_script, monkeypatch):
    def fetch_with_bad_spy(ticker, period="3y"):
        if ticker == "SPY":
            raise RuntimeError("simulated SPY fetch failure")
        return _fake_ohlcv(seed=abs(hash(ticker)) % 1000)

    monkeypatch.setattr(wired_script, "fetch_ohlcv", fetch_with_bad_spy)
    rc = wired_script.main()
    assert rc == 0
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert any(t == "SPY" for t, _err in output["failures"])
    assert set(output["benchmarks"].keys()) == {"QQQ", "TLT"}
    assert len(output["models"]) == 5  # trading models are unaffected by a benchmark-only failure


def test_benchmark_nan_close_is_dropped_not_crashed(wired_script, monkeypatch):
    """Same NaN-gap shape as test_isolated_nan_close_does_not_crash_the_run,
    but asserting the benchmark path specifically: _buy_and_hold_payload
    must drop the NaN day rather than letting it corrupt the equity curve
    or crash the JSON write."""
    def fetch_with_one_nan_close(ticker, period="3y"):
        df = _fake_ohlcv(seed=abs(hash(ticker)) % 1000, vol=6.0)
        df = df.copy()
        df.iloc[len(df) // 2, df.columns.get_loc("Close")] = float("nan")
        return df

    monkeypatch.setattr(wired_script, "fetch_ohlcv", fetch_with_one_nan_close)
    rc = wired_script.main()
    assert rc == 0
    raw = wired_script.OUTPUT_PATH.read_text(encoding="utf-8")
    assert "NaN" not in raw
    output = json.loads(raw)
    assert set(output["benchmarks"].keys()) == {"SPY", "QQQ", "TLT"}
    for payload in output["benchmarks"].values():
        assert payload["total_return"] is not None


def test_buy_and_hold_payload_pure_function(wired_script):
    idx = pd.bdate_range("2024-01-01", periods=5)
    ohlcv = pd.DataFrame({"Close": [100.0, 110.0, 105.0, 120.0, 130.0]}, index=idx)
    payload = wired_script._buy_and_hold_payload(ohlcv, "테스트 벤치마크", "Q")
    assert payload["label"] == "테스트 벤치마크"
    assert payload["n_trades"] == 1
    assert payload["starting_cash"] == wired_script.STARTING_CASH
    assert payload["final_equity"] == pytest.approx(wired_script.STARTING_CASH * 130.0 / 100.0)
    assert payload["total_return"] == pytest.approx(0.3)
    # peak 100 -> trough 105 dip is never the worst: 110 -> 105 is a -4.5%
    # decline, smaller in magnitude than the swing off the actual peak (130
    # is the final value here, so the only real drawdown is 110 -> 105).
    assert payload["max_drawdown"]["max_drawdown_pct"] == pytest.approx(105.0 / 110.0 - 1.0)


# -- data_quality / NaN Close frequency (2026-09-23 follow-up) --
#
# The 2026-09-22 guards above (test_isolated_nan_close_does_not_crash_the_run,
# test_benchmark_nan_close_is_dropped_not_crashed) only prove a NaN Close is
# safely SKIPPED, not how often it actually happens. These tests cover the
# separate _nan_close_count() measurement and the "data_quality" field it
# feeds, added so a future session (or 현우) can see the underlying
# yfinance data-gap frequency directly instead of it being silently
# swallowed by the skip guards.

def test_nan_close_count_helper():
    idx = pd.bdate_range("2024-01-01", periods=5)
    clean = pd.DataFrame({"Close": [100.0, 101.0, 102.0, 103.0, 104.0]}, index=idx)
    assert script._nan_close_count(clean) == 0

    with_gaps = pd.DataFrame({"Close": [100.0, np.nan, 102.0, np.nan, 104.0]}, index=idx)
    assert script._nan_close_count(with_gaps) == 2

    no_close_column = pd.DataFrame({"Open": [1.0, 2.0]})
    assert script._nan_close_count(no_close_column) == 0


def test_data_quality_reports_zero_when_history_is_clean(wired_script, monkeypatch):
    # Clean synthetic history still has NaN quant_score/indicator values
    # during each ticker's warm-up window (rolling windows need lookback) --
    # data_quality must report zero regardless, since it measures NaN
    # *Close* prices specifically, not warm-up NaNs in derived indicators.
    _mock_fetch(monkeypatch, wired_script)
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))

    dq = output["data_quality"]
    assert dq["total_nan_close_days"] == 0
    assert set(dq["nan_close_days_by_ticker"].keys()) == {
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "QQQ", "SPY", "TLT",
    }
    assert all(count == 0 for count in dq["nan_close_days_by_ticker"].values())


def test_data_quality_counts_isolated_nan_close_days(wired_script, monkeypatch):
    # Same NaN-injection shape as test_isolated_nan_close_does_not_crash_
    # the_run: every fetched ticker (7 M7 names + QQQ for the market-regime
    # fetch + SPY/TLT for the benchmarks -- QQQ's own benchmark entry
    # reuses the regime fetch rather than fetching again) gets exactly one
    # NaN Close planted at the same relative position, so each should be
    # counted exactly once and the total should be exactly 10.
    def fetch_with_one_nan_close(ticker, period="3y"):
        df = _fake_ohlcv(seed=abs(hash(ticker)) % 1000, vol=6.0)
        df = df.copy()
        df.iloc[len(df) // 2, df.columns.get_loc("Close")] = float("nan")
        return df

    monkeypatch.setattr(wired_script, "fetch_ohlcv", fetch_with_one_nan_close)
    rc = wired_script.main()
    assert rc == 0

    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    dq = output["data_quality"]
    assert dq["total_nan_close_days"] == 10
    assert all(count == 1 for count in dq["nan_close_days_by_ticker"].values())
    assert set(dq["nan_close_days_by_ticker"].keys()) == {
        "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "QQQ", "SPY", "TLT",
    }


def test_data_quality_present_even_when_all_tickers_fail(wired_script, monkeypatch):
    # run_backtest()'s early-return path (no ticker_data at all) must still
    # produce a well-formed data_quality field so dashboard/consumer code
    # never has to special-case a totally-failed run.
    def always_fails(ticker, period="3y"):
        raise RuntimeError("simulated total outage")

    monkeypatch.setattr(wired_script, "fetch_ohlcv", always_fails)
    result = wired_script.run_backtest()
    assert result["data_quality"] == {"nan_close_days_by_ticker": {}, "total_nan_close_days": 0}

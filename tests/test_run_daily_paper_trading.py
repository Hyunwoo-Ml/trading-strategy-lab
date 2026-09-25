import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

from sw2.compare import ComparisonResult  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_daily_paper_trading as script  # noqa: E402


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    """Points the script's module-level paths at a scratch directory so
    tests never touch the real data/ folder, then reloads it fresh."""
    importlib.reload(script)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "SIGNALS_PATH", data_dir / "signals" / "latest.csv")
    monkeypatch.setattr(script, "PORTFOLIOS_DIR", data_dir / "sw2" / "portfolios")
    monkeypatch.setattr(script, "EQUITY_DIR", data_dir / "sw2" / "equity")
    monkeypatch.setattr(script, "COMPARISONS_DIR", data_dir / "sw2" / "comparisons")
    # Point price-criteria paths at an empty scratch dir too -- otherwise
    # these tests would pick up the real repo's sw1/config/price_criteria_models/*.json.
    monkeypatch.setattr(script, "PRICE_CRITERIA_DIR", data_dir / "sw1" / "price_criteria")
    monkeypatch.setattr(script, "PRICE_CRITERIA_CONFIG_DIR", tmp_path / "no_price_criteria_configs")
    # 2026-09-13: market-context inputs also point at empty scratch dirs, so
    # these tests get market_regime=None / earnings_dates={} (i.e. "no
    # filter applied") instead of picking up anything from the real repo.
    monkeypatch.setattr(script, "INDICATORS_DIR", data_dir / "indicators")
    monkeypatch.setattr(script, "MARKET_DIR", data_dir / "market")
    script.SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return script


# 2026-09-25: the script now takes its run date from the signals file's
# `date` column (the market date) and skips a date it already processed,
# so each write_signals() call stamps the next business day by default --
# i.e. every main() call below is a distinct trading day, like production.
_SIGNAL_DATES = pd.bdate_range("2026-09-01", periods=60)


def write_signals(mod, rows):
    call_index = getattr(mod, "_test_signal_calls", 0)
    mod._test_signal_calls = call_index + 1
    date = _SIGNAL_DATES[call_index].date().isoformat()
    rows = [{"date": date, **row} for row in rows]
    pd.DataFrame(rows).to_csv(mod.SIGNALS_PATH, index=False)
    return date


def test_missing_signals_file_returns_error(wired_script):
    assert wired_script.main() == 1


def test_strong_buy_signal_opens_position_for_baseline(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    assert wired_script.main() == 0

    state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert "AAPL" in state["positions"]
    assert state["cash"] < state["starting_cash"]


def test_technical_only_and_baseline_can_diverge_on_news(wired_script):
    # negative news should be able to keep baseline out of a buy that
    # technical_only (which ignores news) still takes
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.35, "news_score": -1.0}])
    assert wired_script.main() == 0

    baseline_state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    technical_state = json.loads((wired_script.PORTFOLIOS_DIR / "technical_only.json").read_text())
    assert "AAPL" not in baseline_state["positions"]  # dragged below buy_1 by bad news
    assert "AAPL" in technical_state["positions"]  # unaffected, quant_score alone clears buy_1


def test_price_crash_triggers_stop_loss_on_next_run(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    wired_script.main()

    write_signals(wired_script, [{"ticker": "AAPL", "close": 180.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()

    state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert "AAPL" not in state["positions"]
    trades = state["trades"]
    assert trades[-1]["action"] == "stop_loss"


def test_portfolio_state_persists_across_runs(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    wired_script.main()
    first_trade_count = len(json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())["trades"])

    write_signals(wired_script, [{"ticker": "AAPL", "close": 202.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()
    second_trade_count = len(json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())["trades"])

    # hold signal on day 2 shouldn't add trades, but the position from day 1 should still be there
    assert second_trade_count == first_trade_count
    state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert "AAPL" in state["positions"]


def test_conservative_tighter_stop_loss_buys_larger_tranche_than_baseline(wired_script):
    # Task #22: risk-based sizing. conservative's stop_loss_pct (-0.05) is
    # tighter than baseline's (-0.07), so for the same buy_1 signal it
    # should commit MORE dollars (bigger tranche), not the same fixed
    # amount -- see sw2.sizing.risk_based_tranche_dollars.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    assert wired_script.main() == 0

    baseline_state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    conservative_state = json.loads((wired_script.PORTFOLIOS_DIR / "conservative.json").read_text())
    baseline_spent = baseline_state["starting_cash"] - baseline_state["cash"]
    conservative_spent = conservative_state["starting_cash"] - conservative_state["cash"]
    assert conservative_spent > baseline_spent


def test_writes_equity_curve_csv_per_model(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    wired_script.main()
    for name in ("baseline", "technical_only", "conservative"):
        equity_df = pd.read_csv(wired_script.EQUITY_DIR / f"{name}.csv")
        assert len(equity_df) == 1


def test_comparisons_file_grows_once_enough_history_exists(wired_script):
    # daily_return's first value is always NaN (pct_change needs a prior
    # row), so it takes 3 equity points to get 2 valid daily-return
    # observations per model -- the minimum compare_daily_returns needs.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    wired_script.main()
    first = json.loads((wired_script.COMPARISONS_DIR / "latest.json").read_text())
    assert first["comparisons"] == []  # only 1 equity point yet -- 0 valid daily returns

    write_signals(wired_script, [{"ticker": "AAPL", "close": 202.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()
    second = json.loads((wired_script.COMPARISONS_DIR / "latest.json").read_text())
    assert second["comparisons"] == []  # 2 equity points -- only 1 valid daily return, still not enough

    write_signals(wired_script, [{"ticker": "AAPL", "close": 205.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()
    third = json.loads((wired_script.COMPARISONS_DIR / "latest.json").read_text())
    assert len(third["comparisons"]) == 3  # 3 equity points -> 2 valid daily returns -> 3 models -> 3 pairs


def test_comparisons_carry_governance_verdict(wired_script):
    # Task #25's sw2.governance.evaluate_promotion wired into the daily
    # comparisons write (2026-09-16) -- every comparison the pipeline
    # writes should now carry a nested "governance" verdict alongside the
    # existing stats, not just a library function nobody calls.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])
    wired_script.main()
    write_signals(wired_script, [{"ticker": "AAPL", "close": 202.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()
    write_signals(wired_script, [{"ticker": "AAPL", "close": 205.0, "quant_score": 0.0, "news_score": None}])
    wired_script.main()

    data = json.loads((wired_script.COMPARISONS_DIR / "latest.json").read_text())
    assert len(data["comparisons"]) == 3
    for c in data["comparisons"]:
        gov = c["governance"]
        assert gov is not None
        assert gov["model_a"] == c["model_a"]
        assert gov["model_b"] == c["model_b"]
        # only 2 valid daily-return observations per model here, far below
        # sw2.governance.GOVERNANCE_MIN_N (20) -- the conservative verdict
        # for "not enough history yet" must be insufficient_data, and must
        # never be misread as "hold" (which would imply enough evidence
        # existed to actively reject promotion).
        assert gov["verdict"] == "insufficient_data"
        assert any("sample size" in r for r in gov["reasons"])


def test_comparison_to_dict_embeds_promote_verdict_when_criteria_met(wired_script):
    # Unit-level check on the new serialization helper itself, using a
    # synthetic ComparisonResult that clears all three of
    # evaluate_promotion's bars (n>=20, significant adjusted p-value,
    # model_a's mean return ahead of model_b's) -- exercises the
    # "promote" path that the day-to-day integration tests above can't
    # reach without 20+ days of scripted history.
    comparison = ComparisonResult(
        model_a="a",
        model_b="b",
        mean_daily_return_a=0.01,
        mean_daily_return_b=-0.002,
        t_statistic=6.0,
        p_value=0.001,
        n_a=25,
        n_b=25,
        p_value_adjusted=0.01,
    )
    result = script._comparison_to_dict(comparison, {})
    assert result["model_a"] == "a"  # existing ComparisonResult fields still present, unchanged
    assert result["governance"]["verdict"] == "promote"
    assert result["governance"]["model_a"] == "a"


# -- price-criteria models (sw1.criteria.generator / sw2.price_criteria_model) --

def write_price_criteria_config(mod, model_name="price_model_1", **overrides):
    mod.PRICE_CRITERIA_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"model_name": model_name, "stop_loss_pct": -0.08, "reward_risk_ratio": 2.0, "buy2_lookback_days": 60}
    payload.update(overrides)
    (mod.PRICE_CRITERIA_CONFIG_DIR / f"{model_name}.json").write_text(json.dumps(payload), encoding="utf-8")


def write_price_criteria_latest(mod, model_name, rows):
    model_dir = mod.PRICE_CRITERIA_DIR / model_name
    model_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(model_dir / "latest.csv", index=False)


def make_criteria_row(ticker="AAPL", close=200.0, buy_1_price=210.0, buy_2_price=190.0, stop_loss_pct=-0.08, target_price=240.0):
    return {
        "model_name": "price_model_1",
        "ticker": ticker,
        "date": "2026-09-13",
        "close": close,
        "buy_1_price": buy_1_price,
        "buy_2_price": buy_2_price,
        "stop_loss_pct": stop_loss_pct,
        "target_price": target_price,
        "basis_buy_1": "test",
        "basis_buy_2": "test",
        "basis_target": "test",
    }


def test_price_criteria_model_skipped_if_latest_csv_missing(wired_script, capsys):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    # no latest.csv written for price_model_1
    assert wired_script.main() == 0
    assert "skipping" in capsys.readouterr().out
    assert not (wired_script.PORTFOLIOS_DIR / "price_model_1.json").exists()


def test_price_criteria_model_opens_position_when_price_at_buy1(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    # close (200) is below buy_1_price (210) -> should trigger buy_1
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


def test_price_criteria_model_holds_when_price_above_buy1(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    # close (220) is above buy_1_price (210) -> nothing should happen
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=220.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert state["positions"] == {}
    assert state["trades"] == []


def test_price_criteria_model_tighter_stop_loss_pct_buys_larger_tranche(wired_script):
    # Task #22: process_price_criteria_model_for_day recomputes tranche size
    # per row from that row's own criteria.stop_loss_pct (unlike the
    # quant-score models, where it's fixed per model) -- two price-criteria
    # models differing only in stop_loss_pct should buy different-sized
    # tranches on the same buy_1 touch.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script, model_name="price_model_1", stop_loss_pct=-0.05)
    write_price_criteria_config(wired_script, model_name="price_model_2", stop_loss_pct=-0.15)
    write_price_criteria_latest(
        wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0, stop_loss_pct=-0.05)]
    )
    write_price_criteria_latest(
        wired_script, "price_model_2", [make_criteria_row(close=200.0, buy_1_price=210.0, stop_loss_pct=-0.15)]
    )

    assert wired_script.main() == 0
    tight_state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    wide_state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_2.json").read_text())
    tight_spent = tight_state["starting_cash"] - tight_state["cash"]
    wide_spent = wide_state["starting_cash"] - wide_state["cash"]
    assert tight_spent > wide_spent


def test_price_criteria_model_participates_in_comparisons(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row()])
    wired_script.main()
    # each main() is a new trading day -> fresh signals file (new market date)
    write_signals(wired_script, [{"ticker": "AAPL", "close": 201.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=201.0)])
    wired_script.main()
    write_signals(wired_script, [{"ticker": "AAPL", "close": 202.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=202.0)])
    wired_script.main()

    third = json.loads((wired_script.COMPARISONS_DIR / "latest.json").read_text())
    model_names = {c["model_a"] for c in third["comparisons"]} | {c["model_b"] for c in third["comparisons"]}
    assert "price_model_1" in model_names


# -- 2026-09-13 feedback: market regime / event blackout wired into the daily run --


def write_market_regime(mod, regime="risk_on"):
    mod.MARKET_DIR.mkdir(parents=True, exist_ok=True)
    (mod.MARKET_DIR / "regime.json").write_text(json.dumps({"regime": regime}), encoding="utf-8")


def write_earnings_dates(mod, dates: dict):
    mod.MARKET_DIR.mkdir(parents=True, exist_ok=True)
    (mod.MARKET_DIR / "earnings_dates.json").write_text(json.dumps(dates), encoding="utf-8")


def test_risk_off_market_regime_blocks_price_criteria_buy1(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])
    write_market_regime(wired_script, regime="risk_off")

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    # close (200) is below buy_1 (210) -- would normally trigger buy_1 --
    # but risk_off should hold it back entirely.
    assert state["positions"] == {}
    assert state["trades"] == []


def test_risk_on_market_regime_allows_price_criteria_buy1(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])
    write_market_regime(wired_script, regime="risk_on")

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


def test_missing_market_regime_file_applies_no_filter(wired_script):
    # No data/market/regime.json written at all -- should behave exactly
    # like before this feature existed (no filter, price condition alone decides).
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


def test_earnings_blackout_blocks_price_criteria_buy1(wired_script):
    run_date = write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script)
    write_price_criteria_latest(
        wired_script, "price_model_1", [make_criteria_row(ticker="AAPL", close=200.0, buy_1_price=210.0)]
    )
    write_earnings_dates(wired_script, {"AAPL": run_date})

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert state["positions"] == {}


# -- 2026-09-14 feedback: sw1/news/input/{TICKER}.txt 시황이 가격기준모델의
# 신규 진입에도 반영되는지 (min_news_score가 설정된 모델에 한해) --


def test_negative_news_score_blocks_price_criteria_buy1_when_gate_configured(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": -0.6}])
    write_price_criteria_config(wired_script, min_news_score=-0.3)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    # close (200) is below buy_1 (210) -- would normally trigger buy_1 --
    # but today's strongly negative news_score should hold it back.
    assert state["positions"] == {}
    assert state["trades"] == []


def test_mildly_negative_news_above_threshold_still_allows_buy1(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": -0.1}])
    write_price_criteria_config(wired_script, min_news_score=-0.3)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


def test_price_criteria_model_ignores_news_gate_by_default(wired_script):
    # min_news_score not set in the config JSON -- must behave exactly as
    # before this feature existed, even with a very negative news_score.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": -0.9}])
    write_price_criteria_config(wired_script)  # no min_news_score override
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


def test_missing_news_score_for_ticker_does_not_block_even_with_gate_configured(wired_script):
    # No sw1/news/input/AAPL.txt this week -> news_score is None in
    # signals -- a configured gate must not block on "no news", only on
    # actually-negative news.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    write_price_criteria_config(wired_script, min_news_score=-0.3)
    write_price_criteria_latest(wired_script, "price_model_1", [make_criteria_row(close=200.0, buy_1_price=210.0)])

    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "price_model_1.json").read_text())
    assert "AAPL" in state["positions"]


# -- 2026-09-25: idempotent re-runs / market-date run_date ------------------


def test_run_date_comes_from_signals_market_date(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None, "date": "2026-09-18"}])
    assert wired_script.main() == 0
    state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert state["equity_curve"][-1]["date"] == "2026-09-18"
    assert state["trades"][-1]["date"] == "2026-09-18"


def test_rerun_on_same_market_date_is_a_noop(wired_script):
    row = {"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None, "date": "2026-09-18"}
    write_signals(wired_script, [row])
    wired_script.main()
    first = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())

    # manual re-run (e.g. workflow_dispatch) with the same market data
    write_signals(wired_script, [row])
    assert wired_script.main() == 0
    second = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())

    assert second == first  # no duplicate equity point, no extra trade
    equity_df = pd.read_csv(wired_script.EQUITY_DIR / "baseline.csv")
    assert len(equity_df) == 1


def test_rerun_after_same_day_take_profit_does_not_reenter(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None, "date": "2026-09-17"}])
    wired_script.main()
    exit_row = {"ticker": "AAPL", "close": 260.0, "quant_score": 0.9, "news_score": None, "date": "2026-09-18"}
    write_signals(wired_script, [exit_row])
    wired_script.main()
    after_exit = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert "AAPL" not in after_exit["positions"]

    write_signals(wired_script, [exit_row])  # same-day re-run with a still-strong buy score
    wired_script.main()
    after_rerun = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert "AAPL" not in after_rerun["positions"]
    assert len(after_rerun["trades"]) == len(after_exit["trades"])


def test_signals_without_date_column_fall_back_to_wall_clock(wired_script):
    pd.DataFrame([{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}]).to_csv(
        wired_script.SIGNALS_PATH, index=False
    )
    assert wired_script.main() == 0
    today = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).date().isoformat()
    state = json.loads((wired_script.PORTFOLIOS_DIR / "baseline.json").read_text())
    assert state["equity_curve"][-1]["date"] == today

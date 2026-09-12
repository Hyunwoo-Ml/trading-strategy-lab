import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

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
    script.SIGNALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    return script


def write_signals(mod, rows):
    pd.DataFrame(rows).to_csv(mod.SIGNALS_PATH, index=False)


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


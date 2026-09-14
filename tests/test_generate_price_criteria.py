from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_price_criteria as script  # noqa: E402

from .conftest import make_mock_ohlcv
from sw1.indicators.technical import compute_all_technical_indicators


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    importlib.reload(script)
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config" / "price_criteria_models"
    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "INDICATORS_DIR", data_dir / "indicators")
    monkeypatch.setattr(script, "PRICE_CRITERIA_DIR", data_dir / "sw1" / "price_criteria")
    monkeypatch.setattr(script, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(script, "SIGNALS_PATH", data_dir / "signals" / "latest.csv")
    monkeypatch.setattr(script, "M7_TICKERS", ["AAPL", "MSFT"])
    script.INDICATORS_DIR.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)
    return script


def write_indicators(mod, ticker, n=300):
    df = compute_all_technical_indicators(make_mock_ohlcv(n=n, seed=hash(ticker) % 1000))
    df.to_csv(mod.INDICATORS_DIR / f"{ticker}.csv")


def write_config(mod, model_name="model_1", **overrides):
    payload = {"model_name": model_name, "stop_loss_pct": -0.08, "reward_risk_ratio": 2.0, "buy2_lookback_days": 60}
    payload.update(overrides)
    (mod.CONFIG_DIR / f"{model_name}.json").write_text(json.dumps(payload), encoding="utf-8")


def write_signals(mod, rows):
    """rows: list of dicts, e.g. [{"ticker": "AAPL", "news_score": -0.5}]"""
    path = mod.SIGNALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_no_configs_returns_zero_and_warns(wired_script, capsys):
    assert wired_script.main() == 0
    assert "no model configs" in capsys.readouterr().out


def test_missing_indicators_skips_ticker_but_still_succeeds(wired_script):
    write_config(wired_script)
    write_indicators(wired_script, "AAPL")
    # MSFT indicators file intentionally missing
    assert wired_script.main() == 0
    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv")
    assert list(latest["ticker"]) == ["AAPL"]


def test_writes_latest_and_history_csv_with_expected_columns(wired_script):
    write_config(wired_script)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")
    assert wired_script.main() == 0

    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv")
    expected_cols = {
        "model_name", "ticker", "date", "close", "buy_1_price", "buy_2_price",
        "stop_loss_pct", "target_price", "basis_buy_1", "basis_buy_2", "basis_target",
        "news_score", "min_news_score", "news_blocked",
    }
    assert expected_cols.issubset(set(latest.columns))
    assert len(latest) == 2
    assert (latest["buy_2_price"] <= latest["buy_1_price"]).all()
    assert (latest["target_price"] > latest["buy_1_price"]).all()

    history = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "history.csv")
    assert len(history) == 2


def test_multiple_models_each_get_their_own_directory(wired_script):
    write_config(wired_script, model_name="model_1", stop_loss_pct=-0.08, reward_risk_ratio=2.0)
    write_config(wired_script, model_name="model_2", stop_loss_pct=-0.05, reward_risk_ratio=3.0)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")

    assert wired_script.main() == 0
    assert (wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv").exists()
    assert (wired_script.PRICE_CRITERIA_DIR / "model_2" / "latest.csv").exists()


def test_history_appends_across_runs(wired_script):
    write_config(wired_script)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")

    wired_script.main()
    wired_script.main()

    history = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "history.csv")
    assert len(history) == 4  # 2 tickers x 2 runs


# -- 2026-09-14 feedback: 대시보드에서 뉴스 반영 여부를 종목별로 확인할 수 있도록
# news_score/min_news_score/news_blocked를 price-criteria CSV에도 기록 --


def test_no_signals_file_leaves_news_fields_null(wired_script):
    write_config(wired_script, min_news_score=-0.3)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")
    # SIGNALS_PATH intentionally not written
    assert wired_script.main() == 0

    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv")
    assert latest["news_score"].isna().all()
    assert (latest["news_blocked"] == False).all()  # noqa: E712


def test_news_score_below_threshold_marks_blocked(wired_script):
    write_config(wired_script, min_news_score=-0.3)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")
    write_signals(wired_script, [
        {"ticker": "AAPL", "news_score": -0.5},
        {"ticker": "MSFT", "news_score": 0.2},
    ])
    assert wired_script.main() == 0

    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv").set_index("ticker")
    assert latest.loc["AAPL", "news_score"] == -0.5
    assert latest.loc["AAPL", "min_news_score"] == -0.3
    assert bool(latest.loc["AAPL", "news_blocked"]) is True
    assert latest.loc["MSFT", "news_score"] == 0.2
    assert bool(latest.loc["MSFT", "news_blocked"]) is False


def test_gate_off_model_never_marks_blocked_even_with_bad_news(wired_script):
    write_config(wired_script)  # no min_news_score -> gate off
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")
    write_signals(wired_script, [{"ticker": "AAPL", "news_score": -0.9}])
    assert wired_script.main() == 0

    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv").set_index("ticker")
    assert latest.loc["AAPL", "news_score"] == -0.9
    assert pd.isna(latest.loc["AAPL", "min_news_score"])
    assert bool(latest.loc["AAPL", "news_blocked"]) is False


def test_ticker_missing_from_signals_leaves_its_news_score_null(wired_script):
    write_config(wired_script, min_news_score=-0.3)
    write_indicators(wired_script, "AAPL")
    write_indicators(wired_script, "MSFT")
    write_signals(wired_script, [{"ticker": "AAPL", "news_score": -0.9}])  # MSFT absent
    assert wired_script.main() == 0

    latest = pd.read_csv(wired_script.PRICE_CRITERIA_DIR / "model_1" / "latest.csv").set_index("ticker")
    assert pd.isna(latest.loc["MSFT", "news_score"])
    assert bool(latest.loc["MSFT", "news_blocked"]) is False

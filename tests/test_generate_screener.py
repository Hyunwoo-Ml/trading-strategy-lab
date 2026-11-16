from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import generate_screener as script  # noqa: E402

from .conftest import make_mock_ohlcv
from sw1.indicators.technical import compute_all_technical_indicators


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    importlib.reload(script)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "INDICATORS_DIR", data_dir / "indicators")
    monkeypatch.setattr(script, "SIGNALS_PATH", data_dir / "signals" / "latest.csv")
    monkeypatch.setattr(script, "SCREENER_DIR", data_dir / "screener")
    monkeypatch.setattr(script, "M7_TICKERS", ["AAPL", "MSFT"])
    script.INDICATORS_DIR.mkdir(parents=True, exist_ok=True)
    return script


def write_indicators(mod, ticker, n=300, trend_bias=0.0, seed=None):
    """trend_bias nudges the random walk's daily drift so a weekly up/down
    trend can be produced deterministically for the tests that need one --
    make_mock_ohlcv's own daily_returns default (loc=0.0004) is close
    enough to flat that which way the weekly MAs end up ordered is
    seed-sensitive, so tests that care about trend direction build their
    own biased series here instead."""
    if trend_bias:
        import numpy as np

        rng = np.random.default_rng(seed if seed is not None else hash(ticker) % 1000)
        dates = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=n)
        n = len(dates)
        daily_returns = rng.normal(loc=trend_bias, scale=0.006, size=n)
        close = 150.0 * np.cumprod(1 + daily_returns)
        high = close * 1.002
        low = close * 0.998
        ohlcv = pd.DataFrame(
            {"Open": close, "High": high, "Low": low, "Close": close, "Volume": 10_000_000},
            index=dates,
        )
    else:
        ohlcv = make_mock_ohlcv(n=n, seed=seed if seed is not None else hash(ticker) % 1000)
    df = compute_all_technical_indicators(ohlcv)
    df.to_csv(mod.INDICATORS_DIR / f"{ticker}.csv")


def write_signals(mod, rows):
    path = mod.SIGNALS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(path, index=False)


def read_screener(mod):
    return pd.read_csv(mod.SCREENER_DIR / "latest.csv")


def test_no_signals_file_returns_zero_and_warns(wired_script, capsys):
    assert wired_script.main() == 0
    assert "no signals" in capsys.readouterr().out
    assert not (wired_script.SCREENER_DIR / "latest.csv").exists()


def test_missing_indicators_still_produces_a_row_with_no_weekly_trend(wired_script):
    # AAPL has a signals row but no indicators/AAPL.csv -- weekly_trend
    # should degrade to empty (None) rather than skipping the ticker
    # entirely, since the daily signal itself doesn't need the weekly file.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    assert len(out) == 1
    assert out.iloc[0]["ticker"] == "AAPL"
    assert pd.isna(out.iloc[0]["weekly_trend"])
    assert out.iloc[0]["confirmed"] == False  # noqa: E712 -- no weekly_trend means never confirmed


def test_ticker_missing_from_signals_is_skipped(wired_script, capsys):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.0, "news_score": None}])
    # MSFT has no row in signals at all

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    assert list(out["ticker"]) == ["AAPL"]
    assert "no signal row for MSFT" in capsys.readouterr().out


def test_missing_news_score_is_treated_as_neutral_zero(wired_script):
    # quant_score alone (0.9) already clears buy_2 (0.60); if a missing
    # news_score were anything other than neutral 0.0 this would change
    # which side of the blend wins -- confirm it doesn't drag the score down.
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": None}])

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    row = out.iloc[0]
    assert row["integrated_score"] == pytest.approx(0.9 * 0.6)
    assert row["signal"] == "buy_1"  # 0.54 clears buy_1 (0.30) but not buy_2 (0.60)
    assert pd.isna(row["news_score"])  # stays None in the output, not coerced to 0.0


def test_confirmed_requires_weekly_trend_not_down(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": 0.9}])
    # strong downtrend: persistent negative drift -> weekly close well below both MAs, descending
    write_indicators(wired_script, "AAPL", trend_bias=-0.01)

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    row = out.iloc[0]
    assert row["signal"] == "buy_2"  # integrated score clears buy_2 on its own
    assert row["weekly_trend"] == "down"
    assert row["confirmed"] == False  # noqa: E712 -- daily buy fighting a weekly downtrend
    assert row["diamond"] == False  # noqa: E712


def test_diamond_requires_buy_2_and_weekly_uptrend(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": 0.9, "news_score": 0.9}])
    # strong uptrend: persistent positive drift -> weekly close well above both MAs, ascending
    write_indicators(wired_script, "AAPL", trend_bias=0.01)

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    row = out.iloc[0]
    assert row["signal"] == "buy_2"
    assert row["weekly_trend"] == "up"
    assert row["confirmed"] == True  # noqa: E712
    assert row["diamond"] == True  # noqa: E712


def test_caution_signal_is_never_a_diamond_even_with_weekly_uptrend(wired_script):
    write_signals(wired_script, [{"ticker": "AAPL", "close": 200.0, "quant_score": -0.9, "news_score": -0.9}])
    write_indicators(wired_script, "AAPL", trend_bias=0.01)

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    row = out.iloc[0]
    assert row["signal"] == "caution"
    assert row["confirmed"] == False  # noqa: E712 -- caution isn't a buy signal
    assert row["diamond"] == False  # noqa: E712


def test_writes_one_row_per_known_ticker(wired_script):
    write_signals(
        wired_script,
        [
            {"ticker": "AAPL", "close": 200.0, "quant_score": 0.1, "news_score": None},
            {"ticker": "MSFT", "close": 300.0, "quant_score": -0.1, "news_score": None},
        ],
    )

    assert wired_script.main() == 0
    out = read_screener(wired_script)
    assert set(out["ticker"]) == {"AAPL", "MSFT"}
    assert len(out) == 2

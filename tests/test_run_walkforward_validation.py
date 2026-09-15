"""Tests for scripts/run_walkforward_validation.py's wiring.

Only main()'s orchestration is covered here -- the actual math lives in
sw1.validation.walkforward (see tests/test_walkforward.py) and is already
unit-tested there. yfinance is mocked out entirely (no network in tests,
same as tests/test_collect_daily_data.py's ANTHROPIC_API_KEY mocking)."""
import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import run_walkforward_validation as script  # noqa: E402


def _fake_ohlcv(n=500, seed=0):
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2023-01-02", periods=n)
    close = 100 + np.cumsum(rng.randn(n))
    close = np.maximum(close, 1.0)  # keep prices positive
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
    output_path = tmp_path / "data" / "walkforward" / "results.json"
    monkeypatch.setattr(script, "OUTPUT_PATH", output_path)
    return script


def test_main_writes_output_with_all_m7_tickers(wired_script, monkeypatch):
    monkeypatch.setattr(
        wired_script,
        "fetch_ohlcv",
        lambda ticker, period="2y": _fake_ohlcv(seed=hash(ticker) % 1000),
    )
    rc = wired_script.main()
    assert rc == 0

    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert set(output["tickers"].keys()) == set(wired_script.M7_TICKERS)
    assert output["failures"] == []
    assert "overall" in output
    assert output["config"]["horizon_days"] == wired_script.HORIZON_DAYS


def test_each_ticker_has_summary_and_windows(wired_script, monkeypatch):
    monkeypatch.setattr(wired_script, "fetch_ohlcv", lambda ticker, period="2y": _fake_ohlcv(seed=1))
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    for ticker, payload in output["tickers"].items():
        assert "summary" in payload
        assert "windows" in payload
        assert payload["summary"]["n_windows_total"] == len(payload["windows"])
        # ~2 years of data at the default 180d/30d window/step should yield several windows
        assert payload["summary"]["n_windows_total"] > 5


def test_one_ticker_failure_does_not_crash_the_run(wired_script, monkeypatch):
    def flaky_fetch(ticker, period="2y"):
        if ticker == "TSLA":
            raise RuntimeError("simulated fetch failure")
        return _fake_ohlcv(seed=2)

    monkeypatch.setattr(wired_script, "fetch_ohlcv", flaky_fetch)
    rc = wired_script.main()
    assert rc == 0  # partial success -- other tickers still came through

    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert "TSLA" not in output["tickers"]
    assert any(t == "TSLA" for t, _err in output["failures"])
    assert len(output["tickers"]) == len(wired_script.M7_TICKERS) - 1


def test_all_tickers_failing_returns_nonzero(wired_script, monkeypatch):
    monkeypatch.setattr(
        wired_script, "fetch_ohlcv", lambda ticker, period="2y": (_ for _ in ()).throw(RuntimeError("boom"))
    )
    rc = wired_script.main()
    assert rc == 1
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    assert output["tickers"] == {}
    assert len(output["failures"]) == len(wired_script.M7_TICKERS)


def test_overall_pools_windows_across_tickers(wired_script, monkeypatch):
    monkeypatch.setattr(wired_script, "fetch_ohlcv", lambda ticker, period="2y": _fake_ohlcv(seed=3))
    wired_script.main()
    output = json.loads(wired_script.OUTPUT_PATH.read_text(encoding="utf-8"))
    total_per_ticker = sum(t["summary"]["n_windows_total"] for t in output["tickers"].values())
    assert output["overall"]["n_windows_total"] == total_per_ticker

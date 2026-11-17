from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sw1.market.feargreed import breadth_series, fear_greed, label_of, rolling_percentile

IDX = pd.bdate_range(end="2026-11-16", periods=400)


def walk(seed, drift=0.0, vol=0.01, start=100.0):
    rng = np.random.default_rng(seed)
    return pd.Series(start * np.cumprod(1 + rng.normal(drift, vol, len(IDX))), index=IDX)


def test_labels():
    assert [label_of(x) for x in (10, 30, 50, 60, 90)] == ["extreme_fear", "fear", "neutral", "greed", "extreme_greed"]
    assert label_of(None) is None


def test_rolling_percentile_of_a_rising_series_is_100():
    s = pd.Series(np.arange(300, dtype=float), index=IDX[:300])
    p = rolling_percentile(s)
    assert p.iloc[-1] == pytest.approx(100) and p.iloc[:59].isna().all()


def test_index_reads_fear_after_a_selloff():
    spy = walk(1, 0.0005)
    spy.iloc[-25:] = spy.iloc[-26] * np.linspace(1, 0.85, 25)  # 15% slide
    vix = pd.Series(16.0, index=IDX) + walk(2, vol=0.005) - 100
    vix.iloc[-25:] = np.linspace(16, 35, 25)
    vix3m = pd.Series(19.0, index=IDX)
    closes = {"SPY": spy, "^VIX": vix, "^VIX3M": vix3m, "TLT": walk(3), "HYG": walk(4), "LQD": walk(5)}
    fg = fear_greed(closes, breadth=pd.Series(0.15, index=IDX))
    assert fg["score"] < 25 and fg["label"] == "extreme_fear"
    assert {c["key"] for c in fg["components"]} == {"momentum", "breadth", "volatility", "term", "safe_haven", "junk"}
    assert fg["week_ago"] is not None and fg["month_ago"] > fg["score"]
    assert len(fg["history"]) == 120 and fg["as_of"] == "2026-11-16"


def test_missing_inputs_use_what_is_there():
    fg = fear_greed({"SPY": walk(1, 0.001), "^VIX": None})
    assert [c["key"] for c in fg["components"]] == ["momentum"]
    assert fear_greed({}) is None


def test_breadth_series_counts_stocks_above_their_50_day_average():
    up = pd.Series(np.linspace(100, 200, 200), index=IDX[:200])
    down = pd.Series(np.linspace(200, 100, 200), index=IDX[:200])
    b = breadth_series({f"U{i}": up for i in range(3)} | {f"D{i}": down for i in range(2)})
    assert b.iloc[-1] == pytest.approx(0.6)

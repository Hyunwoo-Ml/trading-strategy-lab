from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from sw1.market.flow import flow, move_check, seasonality, sub_scores, volatility


def closes(n=600, seed=3, drift=0.0005, vol=0.02):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end="2026-11-16", periods=n)
    return pd.Series(100 * np.cumprod(1 + rng.normal(drift, vol, n)), index=idx)


def test_volatility_realized_and_premium():
    c = closes(vol=0.02)
    v = volatility(c, atm_iv=0.45)
    assert v["realized_20d"] == pytest.approx(0.02 * np.sqrt(252) * 100, rel=0.35)
    assert 0 <= v["realized_pctile"] <= 1
    assert v["premium"] == pytest.approx(45 - v["realized_20d"], abs=0.11)
    assert volatility(c.iloc[:10]) is None


def test_seasonality_uses_past_years_of_the_same_month():
    idx = pd.bdate_range("2020-01-01", "2026-11-16")
    # every November rises ~5%, other months flat
    steps = np.where(idx.month == 11, 1.05 ** (1 / 21), 1.0)
    c = pd.Series(100 * np.cumprod(steps), index=idx)
    s = seasonality(c)
    assert s["month"] == 11 and s["years"] == 6 and s["up_years"] == 6  # 2020-2025, not 2026 in progress
    assert s["mean_pct"] == pytest.approx(5, abs=0.6) and s["score"] == 5
    assert seasonality(c.iloc[:30]) is None


def test_sub_scores_ranges():
    c = closes()
    s = sub_scores(c, pc_ratio_oi=0.5)
    assert all(0 <= s[k] <= 5 for k in ("momentum", "volatility", "options"))
    assert s["options"] == 4  # 5 * (1 - 0.25) = 3.75 -> 4
    assert sub_scores(c, None)["options"] is None
    assert sub_scores(c, 3.0)["options"] == 0


def test_move_check_needs_an_earlier_snapshot():
    prev = {"expected_move_pct": 1.5, "as_of": "2026-11-13"}
    m = move_check(prev, "2026-11-16", -2.1)
    assert m == {"expected_pct": 1.5, "actual_pct": 2.1, "within": False, "expected_as_of": "2026-11-13", "as_of": "2026-11-16"}
    assert move_check(prev, "2026-11-13", 1.0) is None  # same day: no new information
    assert move_check(None, "2026-11-16", 1.0) is None


def test_flow_bundle():
    f = flow(closes(), {"atm_iv": 0.3, "pc_ratio_oi": 1.0}, "2026-11-16", 0.5)
    assert set(f) == {"volatility", "seasonality", "scores", "move_check"} and f["move_check"] is None

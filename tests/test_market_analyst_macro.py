from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from sw1.market.analyst import target_summary
from sw1.market.macro_fit import macro_fit

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def actions(rows):
    df = pd.DataFrame(rows, columns=["GradeDate", "Firm", "ToGrade", "FromGrade", "Action", "priceTargetAction", "currentPriceTarget", "priorPriceTarget"])
    return df.set_index(pd.to_datetime(df.pop("GradeDate")))


def test_target_summary_counts_recent_moves_only():
    a = actions([
        ("2026-11-10", "Firm A", "Buy", "Hold", "up", "Raises", 200, 150),
        ("2026-11-05", "Firm B", "Buy", "Buy", "main", "Raises", 210, 190),
        ("2026-11-01", "Firm C", "Hold", "Hold", "reit", "Lowers", 140, 160),
        ("2026-08-01", "Firm D", "Sell", "Hold", "down", "Lowers", 100, 150),  # too old
    ])
    s = target_summary(a, {"mean": 180.0, "high": 250, "low": 0, "median": 175}, NOW, spot=150.0)
    assert (s["raises"], s["lowers"], s["upgrades"], s["downgrades"]) == (2, 1, 1, 0)
    assert s["tone"] == "positive"
    assert [r["firm"] for r in s["recent"]] == ["Firm A", "Firm B", "Firm C"]
    assert s["recent"][0]["grade"] == "상향" and s["recent"][0]["target_action"] == "목표가 상향"
    assert s["consensus"]["upside_pct"] == pytest.approx(20.0) and s["consensus"]["low"] is None


def test_target_summary_without_data():
    s = target_summary(None, None, NOW, spot=100.0)
    assert s["tone"] is None and s["recent"] == [] and s["consensus"] is None
    only_old = actions([("2026-01-01", "F", "Buy", "Hold", "up", "Raises", 1, 1)])
    assert target_summary(only_old, None, NOW, 100.0)["tone"] is None


def _series(values, end="2026-11-16"):
    return pd.Series(values, index=pd.bdate_range(end=end, periods=len(values)))


def test_macro_fit_reads_a_rate_sensitive_stock():
    rng = np.random.default_rng(0)
    n = 200
    tnx_chg = rng.normal(0, 0.05, n)
    tnx_chg[-20:] = -0.02  # yields fell 40bp over the last 20 days
    tnx = _series(4.5 + np.cumsum(tnx_chg))
    stock_ret = -0.08 * tnx_chg + rng.normal(0, 0.002, n)  # loses 0.08% per +1bp... falls when yields rise
    stock = _series(100 * np.cumprod(1 + stock_ret))
    dollar = _series(100 * np.cumprod(1 + rng.normal(0, 0.003, n)))
    fit = macro_fit(stock, {"rates": tnx, "dollar": dollar})
    rates = [f for f in fit["factors"] if f["key"] == "rates"][0]
    assert rates["beta"] == pytest.approx(-0.08, abs=0.01)
    assert rates["move"] == pytest.approx(-0.4, abs=0.01) and rates["contribution_pct"] > 0
    assert fit["label"] == "favorable" and {f["key"] for f in fit["factors"]} == {"rates", "dollar"}


def test_macro_fit_needs_enough_history():
    short = _series(np.linspace(100, 110, 40))
    assert macro_fit(short, {"rates": short}) is None
    assert macro_fit(_series(np.linspace(100, 110, 200)), {}) is None

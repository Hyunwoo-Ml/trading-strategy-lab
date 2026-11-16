from __future__ import annotations

import pandas as pd
import pytest

from sw1.indicators.technical import add_rsi, compute_all_technical_indicators
from sw1.indicators.weekly import resample_to_weekly
from sw1.market.signal_stats import (
    DAILY_RULES,
    WEEKLY_RULES,
    SignalRule,
    SignalStat,
    _episodes,
    evaluate_all,
    evaluate_rule,
    headline_signal,
)

from .conftest import make_mock_ohlcv


def frame(closes):
    idx = pd.bdate_range("2025-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


def rule_at(positions, horizon=2, cooldown=0, timeframe="1d", kind="buy"):
    def fires(d):
        s = pd.Series(False, index=d.index)
        s.iloc[positions] = True
        return s
    return SignalRule("t", "test", kind, timeframe, horizon, cooldown, fires)


def test_episodes_merge_fires_within_cooldown():
    fired = pd.Series([True, True, False, True, False, False, False, True])
    assert _episodes(fired, cooldown=2) == [0, 7]  # 1 and 3 continue the episode that started at 0
    assert _episodes(fired, cooldown=0) == [0, 1, 3, 7]


def test_forward_return_baseline_excess_and_win_rate():
    closes = [100, 110, 100, 110, 121, 100, 90, 99]
    stat = evaluate_rule(frame(closes), rule_at([0, 2, 5], horizon=2))
    # fires at 0 -> +0%, 2 -> +21%, 5 -> -1%
    assert stat.n == 3
    assert stat.mean_fwd_pct == pytest.approx((0 + 21 - 1) / 3, abs=0.01)
    assert stat.win_rate == pytest.approx(1 / 3, abs=1e-3)
    fwd = [(closes[i + 2] / closes[i] - 1) * 100 for i in range(len(closes) - 2)]
    assert stat.baseline_pct == pytest.approx(sum(fwd) / len(fwd), abs=0.01)
    assert stat.excess_pct == pytest.approx(stat.mean_fwd_pct - stat.baseline_pct, abs=0.02)
    assert stat.low_sample is True


def test_fires_too_close_to_the_end_are_dated_but_not_scored():
    stat = evaluate_rule(frame([100, 101, 102, 103, 104]), rule_at([4], horizon=2))
    assert stat.n == 0 and stat.mean_fwd_pct is None
    assert stat.bars_ago == 0 and stat.recent is True
    assert stat.last_date == "2025-01-07"


def test_old_fire_is_not_recent():
    closes = list(range(100, 140))
    stat = evaluate_rule(frame(closes), rule_at([5], horizon=2))
    assert stat.bars_ago == 34 and stat.recent is False


def test_rules_never_look_ahead():
    full = make_mock_ohlcv(n=900, seed=7)
    cut = full.iloc[:700]
    d_full, d_cut = compute_all_technical_indicators(full), compute_all_technical_indicators(cut)
    for rule in DAILY_RULES:
        a = rule.fires(d_full).iloc[:700].fillna(False).astype(bool)
        b = rule.fires(d_cut).fillna(False).astype(bool)
        assert (a.to_numpy() == b.to_numpy()).all(), rule.key
    w_full, w_cut = add_rsi(resample_to_weekly(full)), add_rsi(resample_to_weekly(cut))
    n = len(w_cut) - 1  # the cut's last week may be partial
    for rule in WEEKLY_RULES:
        a = rule.fires(w_full).iloc[:n].fillna(False).astype(bool)
        b = rule.fires(w_cut).iloc[:n].fillna(False).astype(bool)
        assert (a.to_numpy() == b.to_numpy()).all(), rule.key


def test_evaluate_all_returns_every_rule_on_a_long_history():
    stats = evaluate_all(make_mock_ohlcv(n=1500, seed=3))
    assert [s.key for s in stats] == [r.key for r in DAILY_RULES] + [r.key for r in WEEKLY_RULES]
    for s in stats:
        assert s.n >= 0
        if s.n:
            assert 0 <= s.win_rate <= 1
    assert sum(s.n for s in stats) > 0  # a random walk trips at least some rules


def _stat(key, kind, bars_ago, timeframe="1d"):
    return SignalStat(key, key, kind, timeframe, 20, 10, 1.0, 0.5, 0.5, 0.6, False, "2026-11-13", bars_ago, True)


def test_headline_prefers_strong_buy_then_recency():
    stats = [_stat("a", "buy", 0), _stat("b", "strong_buy", 6), _stat("c", "sell", 0)]
    assert headline_signal(stats).key == "b"
    assert headline_signal([_stat("a", "buy", 3), _stat("d", "buy", 1)]).key == "d"
    assert headline_signal([]) is None

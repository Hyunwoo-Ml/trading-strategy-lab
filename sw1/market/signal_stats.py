"""
SW1 -- rule-based buy/sell signals with their own historical track record.

Each rule marks the bars where it fires. For every rule we then look back
over the ticker's own price history and report:

    n            how many times it fired (a new fire within `cooldown` bars
                 of the previous one is treated as the same episode)
    mean_fwd     average return `horizon` bars after a fire
    baseline     average `horizon`-bar return over all bars in the same history
    excess       mean_fwd - baseline (did the signal beat just holding?)
    win_rate     share of fires followed by a positive return

so a reader can judge each signal by how it has actually played out on this
ticker, not by a promise. The rules use only data up to the bar they fire
on (no look-ahead). Past behaviour does not guarantee future returns, and
small n means the numbers are noisy -- `low_sample` flags n < MIN_SAMPLE.

The rules are deliberately plain (RSI, Bollinger, MACD, moving averages)
so any of them can be checked on a chart.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd

from sw1.indicators.technical import add_rsi, compute_all_technical_indicators
from sw1.indicators.weekly import resample_to_weekly

MIN_SAMPLE = 8
RECENT_BARS = {"1d": 10, "1w": 2}


@dataclass(frozen=True)
class SignalRule:
    key: str
    label: str
    kind: str  # strong_buy | buy | sell | caution
    timeframe: str  # 1d | 1w
    horizon: int  # bars
    cooldown: int  # bars
    fires: Callable[[pd.DataFrame], pd.Series]


@dataclass
class SignalStat:
    key: str
    label: str
    kind: str
    timeframe: str
    horizon: int
    n: int
    mean_fwd_pct: float | None
    baseline_pct: float | None
    excess_pct: float | None
    win_rate: float | None
    low_sample: bool
    last_date: str | None
    bars_ago: int | None
    recent: bool


def _cross_below(s: pd.Series, level: float) -> pd.Series:
    return (s < level) & (s.shift(1) >= level)


def _cross_above(s: pd.Series, level: float) -> pd.Series:
    return (s > level) & (s.shift(1) <= level)


def _long_uptrend(d: pd.DataFrame) -> pd.Series:
    return (d["MA_50"] > d["MA_200"]) & (d["MA_200"] > d["MA_200"].shift(20))


def _daily_oversold(d: pd.DataFrame) -> pd.Series:
    return _cross_below(d["RSI"], 30) | ((d["Close"] < d["BB_LOWER"]) & (d["Close"].shift(1) >= d["BB_LOWER"].shift(1)))


def _ma20(d: pd.DataFrame) -> pd.Series:
    return d["Close"].rolling(20, min_periods=20).mean()


DAILY_RULES: tuple[SignalRule, ...] = (
    SignalRule("strong_buy_dip", "장기 상승추세 속 과매도 (RSI 30 또는 볼린저 하단)", "strong_buy", "1d", 20, 5,
               lambda d: _daily_oversold(d) & _long_uptrend(d)),
    SignalRule("rsi_oversold", "RSI 30 아래로 진입 (과매도)", "buy", "1d", 20, 5,
               lambda d: _cross_below(d["RSI"], 30)),
    SignalRule("bb_lower_break", "볼린저 밴드 하단 이탈", "buy", "1d", 10, 5,
               lambda d: (d["Close"] < d["BB_LOWER"]) & (d["Close"].shift(1) >= d["BB_LOWER"].shift(1))),
    SignalRule("macd_turn_up", "MACD 0선 아래에서 상승 전환", "buy", "1d", 20, 5,
               lambda d: _cross_above(d["MACD_HIST"], 0) & (d["MACD"] < 0)),
    SignalRule("ma20_reclaim", "상승 추세 중 20일선 회복", "buy", "1d", 20, 5,
               lambda d: _cross_above(d["Close"] - _ma20(d), 0) & (d["Close"] > d["MA_50"]) & (d["MA_50"] > d["MA_50"].shift(10))),
    SignalRule("golden_cross", "골든크로스 (50일선이 200일선 위로)", "buy", "1d", 60, 20,
               lambda d: _cross_above(d["MA_50"] - d["MA_200"], 0)),
    SignalRule("rsi_overbought", "RSI 70 위로 진입 (과매수)", "caution", "1d", 20, 5,
               lambda d: _cross_above(d["RSI"], 70)),
    SignalRule("macd_turn_down", "MACD 0선 위에서 하락 전환", "sell", "1d", 20, 5,
               lambda d: _cross_below(d["MACD_HIST"], 0) & (d["MACD"] > 0)),
    SignalRule("dead_cross", "데드크로스 (50일선이 200일선 아래로)", "sell", "1d", 60, 20,
               lambda d: _cross_below(d["MA_50"] - d["MA_200"], 0)),
)


def _w_ma20(w: pd.DataFrame) -> pd.Series:
    return w["Close"].rolling(20, min_periods=20).mean()


WEEKLY_RULES: tuple[SignalRule, ...] = (
    SignalRule("w_rsi_low", "주봉 RSI 35 아래로 진입", "buy", "1w", 13, 2,
               lambda w: _cross_below(w["RSI"], 35)),
    SignalRule("w_ma20_support", "오르는 20주선 지지 (위에서 내려와 닿음)", "buy", "1w", 13, 4,
               lambda w: (w["Low"] <= _w_ma20(w)) & (w["Close"] >= _w_ma20(w))
               & (w["Low"].shift(1) > _w_ma20(w).shift(1)) & (_w_ma20(w) > _w_ma20(w).shift(4))),
    SignalRule("w_rsi_high", "주봉 RSI 70 위로 진입", "caution", "1w", 13, 2,
               lambda w: _cross_above(w["RSI"], 70)),
)


def _episodes(fired: pd.Series, cooldown: int) -> list[int]:
    """Positions where a new episode starts: a fire more than `cooldown`
    bars after the previous fire."""
    positions = [i for i, v in enumerate(fired.fillna(False).astype(bool).to_numpy()) if v]
    out, last = [], None
    for p in positions:
        if last is None or p - last > cooldown:
            out.append(p)
        last = p
    return out


def evaluate_rule(frame: pd.DataFrame, rule: SignalRule) -> SignalStat:
    close = frame["Close"].astype(float)
    fired = rule.fires(frame)
    starts = _episodes(fired, rule.cooldown)
    h = rule.horizon

    fwd_all = (close.shift(-h) / close - 1) * 100
    baseline = fwd_all.dropna()
    returns = [float(fwd_all.iloc[p]) for p in starts if p + h < len(close)]

    n = len(returns)
    mean_fwd = round(sum(returns) / n, 2) if n else None
    base = round(float(baseline.mean()), 2) if len(baseline) else None
    excess = round(mean_fwd - base, 2) if (mean_fwd is not None and base is not None) else None
    win = round(sum(r > 0 for r in returns) / n, 3) if n else None

    last_pos = starts[-1] if starts else None
    bars_ago = (len(close) - 1 - last_pos) if last_pos is not None else None
    last_idx = frame.index[last_pos] if last_pos is not None else None
    last_date = last_idx.date().isoformat() if hasattr(last_idx, "date") else (str(last_idx) if last_idx is not None else None)

    return SignalStat(
        key=rule.key,
        label=rule.label,
        kind=rule.kind,
        timeframe=rule.timeframe,
        horizon=h,
        n=n,
        mean_fwd_pct=mean_fwd,
        baseline_pct=base,
        excess_pct=excess,
        win_rate=win,
        low_sample=n < MIN_SAMPLE,
        last_date=last_date,
        bars_ago=bars_ago,
        recent=bars_ago is not None and bars_ago < RECENT_BARS[rule.timeframe],
    )


def evaluate_all(daily_ohlcv: pd.DataFrame) -> list[SignalStat]:
    """daily_ohlcv: Open/High/Low/Close/Volume with a tz-naive DatetimeIndex,
    as long a history as available (more history = more reliable stats)."""
    daily = compute_all_technical_indicators(daily_ohlcv)
    weekly = add_rsi(resample_to_weekly(daily_ohlcv))
    stats = [evaluate_rule(daily, r) for r in DAILY_RULES]
    stats += [evaluate_rule(weekly, r) for r in WEEKLY_RULES]
    return stats


def headline_signal(stats: list[SignalStat]) -> SignalStat | None:
    """The single most important *recent* signal for a watchlist row:
    strong_buy beats buy beats sell beats caution; ties go to the most recent."""
    rank = {"strong_buy": 0, "buy": 1, "sell": 2, "caution": 3}
    recent = [s for s in stats if s.recent]
    if not recent:
        return None
    return min(recent, key=lambda s: (rank[s.kind], s.bars_ago if s.timeframe == "1d" else s.bars_ago * 5))

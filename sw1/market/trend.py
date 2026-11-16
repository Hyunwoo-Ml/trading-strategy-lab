"""
SW1 -- trend strength score and trend position for one ticker.

The score is five components, each an integer in -3..+3, summed to a
total in -15..+15:

    short   close vs 20-day MA      (+-1 above/below, +-2 beyond 2%, +-3 beyond 5%)
    mid     close vs 50-day MA      (thresholds 0 / 3% / 7%)
    long    close vs 200-day MA     (thresholds 0 / 5% / 12%)
    stack   MA20 > MA50 > MA200 is +3, fully inverted is -3, otherwise
            +-1 from MA50 vs MA200 alone (0 if equal)
    momentum 20-day return          (thresholds 0 / 4% / 8%)

Labels: >= +11 very strong, >= +6 strong, <= -11 very weak, <= -6 weak,
otherwise neutral. A component that can't be computed yet (short history)
counts 0, so new listings read neutral rather than erroring.

Everything is plain moving-average arithmetic, so a reader can check any
number by hand. It describes the trend; it is not a forecast.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

LABELS = (
    (11, "very_strong"),
    (6, "strong"),
    (-5, "neutral"),
    (-10, "weak"),
)


@dataclass
class TrendStrength:
    score: int  # -15..+15
    label: str  # very_strong | strong | neutral | weak | very_weak
    components: dict[str, int] = field(default_factory=dict)


@dataclass
class TrendPosition:
    close: float
    high_52w: float
    low_52w: float
    pct_from_high: float  # <= 0
    pct_from_low: float  # >= 0
    ma20: float | None
    ma50: float | None
    ma200: float | None
    pct_vs_ma20: float | None
    pct_vs_ma50: float | None
    pct_vs_ma200: float | None
    golden_stack: bool | None  # MA50 above MA200
    return_1d: float | None
    return_5d: float | None
    return_20d: float | None


def _band(value: float | None, t1: float, t2: float) -> int:
    """-3..+3 from a signed percentage and two thresholds."""
    if value is None or pd.isna(value):
        return 0
    mag = abs(value)
    step = 3 if mag > t2 else 2 if mag > t1 else 1 if mag > 0 else 0
    return step if value > 0 else -step


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or pd.isna(a) or pd.isna(b) or b == 0:
        return None
    return (a / b - 1) * 100


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    return _pct(float(close.iloc[-1]), float(close.iloc[-1 - days]))


def _ma(close: pd.Series, window: int) -> float | None:
    if len(close) < window:
        return None
    return float(close.iloc[-window:].mean())


def label_for(score: int) -> str:
    for floor, label in LABELS:
        if score >= floor:
            return label
    return "very_weak"


def compute_trend_strength(close: pd.Series) -> TrendStrength:
    close = close.dropna()
    if len(close) == 0:
        raise ValueError("no closes")
    last = float(close.iloc[-1])
    ma20, ma50, ma200 = _ma(close, 20), _ma(close, 50), _ma(close, 200)

    if ma20 is not None and ma50 is not None and ma200 is not None:
        if ma20 > ma50 > ma200:
            stack = 3
        elif ma20 < ma50 < ma200:
            stack = -3
        else:
            stack = 1 if ma50 > ma200 else -1 if ma50 < ma200 else 0
    else:
        stack = 0

    components = {
        "short": _band(_pct(last, ma20), 2, 5),
        "mid": _band(_pct(last, ma50), 3, 7),
        "long": _band(_pct(last, ma200), 5, 12),
        "stack": stack,
        "momentum": _band(_ret(close, 20), 4, 8),
    }
    score = int(sum(components.values()))
    return TrendStrength(score=score, label=label_for(score), components=components)


def compute_trend_position(close: pd.Series) -> TrendPosition:
    close = close.dropna()
    if len(close) == 0:
        raise ValueError("no closes")
    last = float(close.iloc[-1])
    year = close.iloc[-252:]
    high, low = float(year.max()), float(year.min())
    ma20, ma50, ma200 = _ma(close, 20), _ma(close, 50), _ma(close, 200)

    def r(x):
        return round(x, 2) if x is not None else None

    return TrendPosition(
        close=round(last, 4),
        high_52w=round(high, 4),
        low_52w=round(low, 4),
        pct_from_high=round(_pct(last, high), 2),
        pct_from_low=round(_pct(last, low), 2),
        ma20=r(ma20),
        ma50=r(ma50),
        ma200=r(ma200),
        pct_vs_ma20=r(_pct(last, ma20)),
        pct_vs_ma50=r(_pct(last, ma50)),
        pct_vs_ma200=r(_pct(last, ma200)),
        golden_stack=(ma50 > ma200) if (ma50 is not None and ma200 is not None) else None,
        return_1d=r(_ret(close, 1)),
        return_5d=r(_ret(close, 5)),
        return_20d=r(_ret(close, 20)),
    )

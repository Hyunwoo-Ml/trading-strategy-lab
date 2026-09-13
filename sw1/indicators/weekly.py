"""
SW1 -- weekly-timeframe trend confirmation.

Resamples daily OHLCV into weekly bars and reads a short/long moving
average trend off them, so sw2.price_criteria_model can require the
weekly trend not be in a confirmed downtrend before treating a *daily*
buy_1/buy_2 touch as a real entry. A daily support touch in the middle of
a multi-month weekly downtrend is a very different situation from the same
touch during a weekly uptrend or sideways consolidation -- per 2026-09-13
feedback ("주봉도 같이 바라보는 로직도 추가해줘").
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

DEFAULT_SHORT_WINDOW = 10  # weeks (~2.5 months)
DEFAULT_LONG_WINDOW = 30   # weeks (~7 months) -- slow on purpose so a
                            # single bad daily print can't flip the read


@dataclass
class WeeklyTrend:
    date: str
    trend: str  # "up" | "down" | "flat"
    close: float
    ma_short: float | None
    ma_long: float | None
    reason: str


def resample_to_weekly(daily_df: pd.DataFrame) -> pd.DataFrame:
    """daily_df needs a DatetimeIndex and Open/High/Low/Close/Volume
    columns (the same shape sw1.indicators.technical expects). Weeks end
    Friday to match US market trading weeks."""
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    weekly = daily_df.resample("W-FRI").agg(agg).dropna(subset=["Close"])
    return weekly


def compute_weekly_trend(
    daily_df: pd.DataFrame,
    short_window: int = DEFAULT_SHORT_WINDOW,
    long_window: int = DEFAULT_LONG_WINDOW,
) -> WeeklyTrend:
    if short_window >= long_window:
        raise ValueError("short_window must be less than long_window")

    weekly = resample_to_weekly(daily_df)
    if len(weekly) == 0:
        raise ValueError("daily_df produced no weekly bars")

    weekly = weekly.copy()
    weekly[f"MA_{short_window}"] = weekly["Close"].rolling(window=short_window, min_periods=short_window).mean()
    weekly[f"MA_{long_window}"] = weekly["Close"].rolling(window=long_window, min_periods=long_window).mean()

    last = weekly.iloc[-1]
    close = float(last["Close"])
    ma_short = last.get(f"MA_{short_window}")
    ma_long = last.get(f"MA_{long_window}")

    date_value = weekly.index[-1]
    date_str = date_value.date().isoformat() if hasattr(date_value, "date") else str(date_value)

    if ma_short is None or ma_long is None or pd.isna(ma_short) or pd.isna(ma_long):
        return WeeklyTrend(
            date=date_str,
            trend="flat",
            close=close,
            ma_short=None,
            ma_long=None,
            reason=f"주봉 데이터 {long_window}주 미만 (기본값 flat)",
        )

    ma_short = float(ma_short)
    ma_long = float(ma_long)

    if close < ma_short and ma_short < ma_long:
        return WeeklyTrend(
            date=date_str,
            trend="down",
            close=close,
            ma_short=ma_short,
            ma_long=ma_long,
            reason=(
                f"주봉 종가({close:.2f})가 {short_window}주선({ma_short:.2f}) 아래이고 "
                f"{short_window}주선도 {long_window}주선({ma_long:.2f}) 아래"
            ),
        )
    if close > ma_short and ma_short > ma_long:
        return WeeklyTrend(
            date=date_str,
            trend="up",
            close=close,
            ma_short=ma_short,
            ma_long=ma_long,
            reason=(
                f"주봉 종가({close:.2f})가 {short_window}주선({ma_short:.2f}) 위이고 "
                f"{short_window}주선도 {long_window}주선({ma_long:.2f}) 위"
            ),
        )
    return WeeklyTrend(
        date=date_str,
        trend="flat",
        close=close,
        ma_short=ma_short,
        ma_long=ma_long,
        reason=f"주봉 추세 혼조 (종가 {close:.2f}, {short_window}주선 {ma_short:.2f}, {long_window}주선 {ma_long:.2f})",
    )

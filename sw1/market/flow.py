"""
SW1 -- the extra per-ticker reads on the 현물 page: volatility, seasonality,
0-5 sub-scores, and how yesterday's actual move compared with the move the
options market expected.

Every number here is plain arithmetic on daily closes (and the option
snapshot when there is one), so a reader can check it by hand:

  volatility   20-day realized volatility, annualized, and where it sits in
               its own past year (0-100% percentile). With an option
               snapshot, the volatility premium = ATM implied - realized.
  seasonality  average return of this calendar month in past years and how
               many of those years were up; score -5..+5 (1 point per 1%).
  sub-scores   0..5 each, higher = more of the thing:
                 momentum   percentile of the 20-day return within the past
                            year's rolling 20-day returns
                 volatility percentile of 20-day realized volatility
                 options    call-heavy positioning: P/C (open interest)
                            of 0 -> 5, 2 or more -> 0
  move check   yesterday's 1-day expected move (from the previous run's
               snapshot) next to the actual close-to-close move.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _percentile(series: pd.Series, value: float) -> float | None:
    s = series.dropna()
    if len(s) < 20 or value is None or not math.isfinite(value):
        return None
    return float((s <= value).mean())


def volatility(close: pd.Series, atm_iv: float | None = None) -> dict | None:
    rets = close.pct_change().dropna()
    if len(rets) < 25:
        return None
    rv = rets.rolling(20).std() * math.sqrt(TRADING_DAYS) * 100
    now = float(rv.iloc[-1])
    out = {
        "realized_20d": round(now, 1),
        "realized_pctile": _percentile(rv.iloc[-TRADING_DAYS:], now),
        "implied": round(atm_iv * 100, 1) if atm_iv else None,
    }
    out["premium"] = round(out["implied"] - out["realized_20d"], 1) if out["implied"] is not None else None
    return out


def seasonality(close: pd.Series, month: int | None = None) -> dict | None:
    """Past returns of one calendar month (default: the month of the last bar)."""
    close = close.dropna()
    if len(close) < 40:
        return None
    month = month or close.index[-1].month
    monthly = close.resample("ME").last().pct_change().dropna()
    this_year = close.index[-1].year
    past = monthly[(monthly.index.month == month) & (monthly.index.year < this_year)]
    if len(past) < 3:
        return None
    mean_pct = float(past.mean() * 100)
    return {
        "month": month, "years": int(len(past)), "mean_pct": round(mean_pct, 2),
        "up_years": int((past > 0).sum()),
        "score": int(max(-5, min(5, round(mean_pct)))),
    }


def sub_scores(close: pd.Series, pc_ratio_oi: float | None) -> dict:
    close = close.dropna()
    r20 = close.pct_change(20)
    rets = close.pct_change()
    rv = rets.rolling(20).std()
    def score(series, value):
        p = _percentile(series.iloc[-TRADING_DAYS:], value) if len(series.dropna()) else None
        return None if p is None else int(round(p * 5))
    options = None
    if pc_ratio_oi is not None and math.isfinite(pc_ratio_oi):
        options = int(round(5 * (1 - min(max(pc_ratio_oi, 0.0), 2.0) / 2.0)))
    return {
        "momentum": score(r20, float(r20.iloc[-1])) if len(r20.dropna()) else None,
        "volatility": score(rv, float(rv.iloc[-1])) if len(rv.dropna()) else None,
        "options": options,
    }


def move_check(prev_options: dict | None, as_of: str, change_pct: float | None) -> dict | None:
    """Compare the previous run's expected 1-day move with today's actual move.
    Only when the previous snapshot is from an earlier trading day."""
    if not prev_options or change_pct is None:
        return None
    prev_em, prev_as_of = prev_options.get("expected_move_pct"), prev_options.get("as_of")
    if prev_em is None or not prev_as_of or prev_as_of >= as_of:
        return None
    actual = abs(float(change_pct))
    return {"expected_pct": round(float(prev_em), 2), "actual_pct": round(actual, 2),
            "within": bool(actual <= prev_em), "expected_as_of": prev_as_of, "as_of": as_of}


def flow(close: pd.Series, options: dict | None, as_of: str, change_pct: float | None,
         prev_options: dict | None = None) -> dict:
    opt = options or {}
    return {
        "volatility": volatility(close, opt.get("atm_iv")),
        "seasonality": seasonality(close),
        "scores": sub_scores(close, opt.get("pc_ratio_oi")),
        "move_check": move_check(prev_options, as_of, change_pct),
    }

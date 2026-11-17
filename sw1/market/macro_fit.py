"""
SW1 -- the 매크로 column: did the last month's moves in rates, the dollar and
oil help or hurt this stock, judging by how it has reacted to them before?

  1. Regress the stock's daily returns over the last ~6 months on the daily
     changes of three factors: 10-year yield (^TNX, in %-points), dollar
     index (DX-Y.NYB, %) and WTI oil (CL=F, %). The betas are its sensitivities.
  2. Multiply each beta by that factor's move over the last 20 trading days.
  3. The sum is the return those macro moves "explain". At least +1.5% reads
     유리 (favorable), at most -1.5% 불리 (unfavorable), otherwise 중립.

It is a rough read of macro headwinds and tailwinds, not a forecast.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 120  # trading days for the betas
LOOKBACK = 20  # trading days for "recent" factor moves
MIN_OBS = 60
THRESHOLD_PCT = 1.5

FACTORS = (
    # (key, label, yahoo ticker, how its daily change is measured)
    ("rates", "금리 (10년물)", "^TNX", "diff"),
    ("dollar", "달러", "DX-Y.NYB", "pct"),
    ("oil", "유가 (WTI)", "CL=F", "pct"),
)


def _change(s: pd.Series, how: str, periods: int = 1) -> pd.Series:
    return s.diff(periods) if how == "diff" else s.pct_change(periods)


def macro_fit(stock_close: pd.Series, factor_closes: dict[str, pd.Series]) -> dict | None:
    """`factor_closes`: factor key -> close series (DatetimeIndex, tz-naive)."""
    cols = {"stock": stock_close.pct_change()}
    used = [f for f in FACTORS if factor_closes.get(f[0]) is not None and len(factor_closes[f[0]]) > LOOKBACK]
    if not used:
        return None
    for key, _, _, how in used:
        cols[key] = _change(factor_closes[key], how)
    df = pd.DataFrame(cols).dropna().iloc[-WINDOW:]
    if len(df) < MIN_OBS:
        return None
    x = np.column_stack([np.ones(len(df))] + [df[k].to_numpy() for k, *_ in used])
    beta, *_ = np.linalg.lstsq(x, df["stock"].to_numpy(), rcond=None)
    parts, total = [], 0.0
    for (key, label, _, how), b in zip(used, beta[1:]):
        s = factor_closes[key].dropna()
        move = float(_change(s, how, LOOKBACK).iloc[-1])
        contrib = float(b) * move * 100  # in % of stock return
        total += contrib
        parts.append({
            "key": key, "label": label, "beta": round(float(b), 3),
            "move": round(move if how == "diff" else move * 100, 2), "move_unit": "%p" if how == "diff" else "%",
            "contribution_pct": round(contrib, 2),
        })
    label = "favorable" if total >= THRESHOLD_PCT else "unfavorable" if total <= -THRESHOLD_PCT else "neutral"
    return {"label": label, "total_pct": round(total, 2), "factors": parts, "window": len(df), "lookback": LOOKBACK}

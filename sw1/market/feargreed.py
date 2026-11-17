"""
SW1 -- Fin-tip_lab's own fear & greed index (0 = extreme fear, 100 = extreme
greed), built only from free daily closes. It is not CNN's index and does
not try to reproduce it.

Six components, each turned into a 0-100 score:

  momentum     SPY vs its 125-day average              (higher = greed)
  breadth      share of watchlist stocks above their 50-day average
               (used directly as 0-100)
  volatility   VIX vs its 50-day average               (higher VIX = fear)
  term         VIX / VIX3M                              (backwardation = fear)
  safe_haven   SPY 20-day return minus TLT 20-day return
  junk         HYG 20-day return minus LQD 20-day return
                                                        (junk bonds lagging = fear)

Except breadth, each raw series is scored as its percentile rank within its
own trailing year, so "50" means "as usual for the past year". The index is
the plain average of whatever components are available.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

WINDOW = 252
MIN_PERIODS = 60
HISTORY_POINTS = 120

COMPONENTS = (
    ("momentum", "주가 모멘텀 (S&P 500 vs 125일 평균)"),
    ("breadth", "시장 폭 (50일선 위 종목 비율)"),
    ("volatility", "변동성 (VIX vs 50일 평균)"),
    ("term", "변동성 기간구조 (VIX ÷ VIX3M)"),
    ("safe_haven", "안전자산 수요 (주식 − 장기채 20일)"),
    ("junk", "정크본드 수요 (하이일드 − 투자등급 20일)"),
)

LABELS = ((25, "extreme_fear"), (45, "fear"), (56, "neutral"), (76, "greed"), (101, "extreme_greed"))


def label_of(score: float | None) -> str | None:
    if score is None or not np.isfinite(score):
        return None
    return next(name for limit, name in LABELS if score < limit)


def rolling_percentile(s: pd.Series, window: int = WINDOW, min_periods: int = MIN_PERIODS) -> pd.Series:
    """Percentile rank (0-100) of each value within its trailing window."""
    s = s.dropna()
    return s.rolling(window, min_periods=min_periods).apply(lambda x: (x <= x[-1]).mean() * 100, raw=True)


def raw_components(closes: dict[str, pd.Series], breadth: pd.Series | None = None) -> dict[str, pd.Series]:
    """`closes`: ticker -> close series (SPY, ^VIX, ^VIX3M, TLT, HYG, LQD).
    `breadth`: daily share (0-1) of stocks above their 50-day average."""
    c = {k: v.dropna() for k, v in closes.items() if v is not None and len(v.dropna())}
    out = {}
    if "SPY" in c:
        out["momentum"] = c["SPY"] / c["SPY"].rolling(125).mean() - 1
    if "^VIX" in c:
        out["volatility"] = -(c["^VIX"] / c["^VIX"].rolling(50).mean() - 1)
    if "^VIX" in c and "^VIX3M" in c:
        out["term"] = -(c["^VIX"] / c["^VIX3M"]).dropna()
    if "SPY" in c and "TLT" in c:
        out["safe_haven"] = (c["SPY"].pct_change(20) - c["TLT"].pct_change(20)).dropna()
    if "HYG" in c and "LQD" in c:
        out["junk"] = (c["HYG"].pct_change(20) - c["LQD"].pct_change(20)).dropna()
    if breadth is not None and len(breadth.dropna()):
        out["breadth"] = breadth.dropna()
    return {k: v.dropna() for k, v in out.items() if len(v.dropna())}


def component_scores(raw: dict[str, pd.Series]) -> dict[str, pd.Series]:
    return {k: (v * 100 if k == "breadth" else rolling_percentile(v)).dropna() for k, v in raw.items()}


def breadth_series(closes_by_ticker: dict[str, pd.Series]) -> pd.Series | None:
    """Daily share of tickers closing above their own 50-day average."""
    flags = []
    for s in closes_by_ticker.values():
        s = s.dropna()
        if len(s) > 60:
            ma = s.rolling(50).mean()
            flags.append((s > ma).astype(float).where(ma.notna()))
    if not flags:
        return None
    df = pd.concat(flags, axis=1)
    counts = df.notna().sum(axis=1)
    enough = counts >= max(1, min(5, len(flags)), len(flags) // 2)
    return (df.sum(axis=1)[enough] / counts[enough]).astype(float)


def fear_greed(closes: dict[str, pd.Series], breadth: pd.Series | None = None) -> dict | None:
    scores = component_scores(raw_components(closes, breadth))
    if not scores:
        return None
    frame = pd.DataFrame(scores).sort_index()
    index = frame.mean(axis=1, skipna=True).dropna()
    if not len(index):
        return None
    last = frame.iloc[-1]

    def ago(n):
        return round(float(index.iloc[-1 - n]), 1) if len(index) > n else None

    score = round(float(index.iloc[-1]), 1)
    return {
        "as_of": index.index[-1].date().isoformat(),
        "score": score,
        "label": label_of(score),
        "week_ago": ago(5),
        "month_ago": ago(21),
        "components": [
            {"key": k, "label": lab, "score": None if pd.isna(last.get(k)) else round(float(last[k]), 1)}
            for k, lab in COMPONENTS if k in frame.columns
        ],
        "history": [{"d": d.date().isoformat(), "v": round(float(v), 1)} for d, v in index.iloc[-HISTORY_POINTS:].items()],
    }

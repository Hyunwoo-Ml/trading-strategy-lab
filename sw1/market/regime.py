"""
SW1 -- market-wide regime detection.

Individual M7 technical support levels (sw1.criteria.generator) say nothing
about whether the market as a whole is in a state where "buy the dip"
historically works. This module gives a simple, explainable market-regime
read using a broad tech index (QQQ by default) so SW2's price-criteria
decisions (sw2.price_criteria_model) can skip *new* entries during a
market-wide risk-off period, per 2026-09-13 feedback ("시장 전체 지표 변화,
상관관계를 반영해줘").

Design note: kept deliberately simple (one broad index, two moving
averages) rather than a multi-factor risk model -- the goal is an
interpretable, auditable filter a person can sanity-check by eye, not a
proprietary regime classifier. Tune/replace once there's backtest evidence
for a better rule (see Task #23, the backtest pipeline).
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

MARKET_INDEX_TICKER = "QQQ"


@dataclass
class MarketRegime:
    date: str
    regime: str  # "risk_on" | "risk_off"
    index_ticker: str
    close: float
    ma_50: float | None
    ma_200: float | None
    reason: str


def compute_market_regime(
    index_indicators_df: pd.DataFrame,
    index_ticker: str = MARKET_INDEX_TICKER,
) -> MarketRegime:
    """index_indicators_df must be the output of
    sw1.indicators.technical.compute_all_technical_indicators run on the
    market index's OHLCV (so MA_50/MA_200 columns are present).

    Rule: risk_off when the index closes below its own 50-day MA AND the
    50-day MA is itself below the 200-day MA -- i.e. a *confirmed*
    downtrend, not just a one-day dip. risk_on otherwise. Missing MAs
    (short history, e.g. early in a backtest) default to risk_on so a
    cold start doesn't block all trading before there's enough data to
    judge the trend.
    """
    if len(index_indicators_df) == 0:
        raise ValueError("index_indicators_df must not be empty")

    last = index_indicators_df.iloc[-1]
    close = float(last["Close"])
    ma_50 = last.get("MA_50")
    ma_200 = last.get("MA_200")

    date_value = index_indicators_df.index[-1]
    date_str = date_value.date().isoformat() if hasattr(date_value, "date") else str(date_value)

    if ma_50 is None or ma_200 is None or pd.isna(ma_50) or pd.isna(ma_200):
        return MarketRegime(
            date=date_str,
            regime="risk_on",
            index_ticker=index_ticker,
            close=close,
            ma_50=None,
            ma_200=None,
            reason=f"{index_ticker} 이동평균 데이터 부족 (기본값 risk_on)",
        )

    ma_50 = float(ma_50)
    ma_200 = float(ma_200)

    if close < ma_50 and ma_50 < ma_200:
        return MarketRegime(
            date=date_str,
            regime="risk_off",
            index_ticker=index_ticker,
            close=close,
            ma_50=ma_50,
            ma_200=ma_200,
            reason=(
                f"{index_ticker} 종가({close:.2f})가 50일선({ma_50:.2f}) 아래이고 "
                f"50일선도 200일선({ma_200:.2f}) 아래 -- 확인된 하락추세"
            ),
        )

    return MarketRegime(
        date=date_str,
        regime="risk_on",
        index_ticker=index_ticker,
        close=close,
        ma_50=ma_50,
        ma_200=ma_200,
        reason=f"{index_ticker} 추세 양호 (종가 {close:.2f}, 50일선 {ma_50:.2f}, 200일선 {ma_200:.2f})",
    )

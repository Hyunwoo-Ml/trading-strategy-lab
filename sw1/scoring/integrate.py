"""
SW1 -- integrated scoring and trade-rule module.

Combines the technical/fundamental indicators (sw1.indicators.technical)
with the news sentiment score (sw1.news.scorer) into one integrated score
in [-1, +1], then turns that score (plus, optionally, price vs. entry)
into a trade signal: 1st/2nd buy tranche, hold, stop-loss, take-profit.

IMPORTANT -- the original 자소서 describes this logic ("이벤트 중요도에 따라
가중치를 부여") but doesn't pin down exact numbers. Everything in DEFAULTS
below is a reasonable starting point chosen for this build, not a value
taken from the original document -- expect to tune it once SW2 has real
Daily 수익률 data to check these against.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class ScoringWeights:
    """Weights for the four rule-based technical sub-scores (must sum to 1.0)
    and for the quant-vs-news blend at the top level."""

    rsi: float = 0.25
    macd: float = 0.25
    bollinger: float = 0.25
    ma_cross: float = 0.25

    quant_blend: float = 0.6   # weight of the technical score in the final blend
    news_blend: float = 0.4    # weight of the news sentiment score

    def validate(self) -> None:
        technical_total = self.rsi + self.macd + self.bollinger + self.ma_cross
        if abs(technical_total - 1.0) > 1e-6:
            raise ValueError(f"technical sub-weights must sum to 1.0, got {technical_total}")
        blend_total = self.quant_blend + self.news_blend
        if abs(blend_total - 1.0) > 1e-6:
            raise ValueError(f"quant_blend + news_blend must sum to 1.0, got {blend_total}")


@dataclass
class TradeThresholds:
    """Default trade-rule thresholds -- tune these once SW2 has enough
    Daily 수익률 history to compare configurations against each other."""

    buy_1_score: float = 0.30      # integrated score >= this -> 1차 매수
    buy_2_score: float = 0.60      # integrated score >= this -> 2차 매수 (add to position)
    caution_score: float = -0.30   # integrated score <= this -> 관망/비중 축소 신호
    stop_loss_pct: float = -0.07   # price return from entry <= this -> 손절
    take_profit_pct: float = 0.15  # price return from entry >= this -> 익절


DEFAULT_WEIGHTS = ScoringWeights()
DEFAULT_THRESHOLDS = TradeThresholds()


def _score_rsi(rsi: float) -> float:
    """RSI 0-100 -> bullish/bearish score in [-1, 1]. Low RSI (oversold) is
    bullish, high RSI (overbought) is bearish."""
    return max(-1.0, min(1.0, (50.0 - rsi) / 50.0))


def _score_macd(macd_hist: float, scale: float = 1.0) -> float:
    """MACD histogram -> [-1, 1] via tanh so a big momentum spike saturates
    instead of dominating the blend. `scale` should be set relative to the
    ticker's typical histogram magnitude; 1.0 is a placeholder default."""
    import math

    return math.tanh(macd_hist / scale) if scale else 0.0


def _score_bollinger(pct_b: float) -> float:
    """%B in [0, 1] (0=lower band, 1=upper band) -> bullish/bearish score.
    Extends past [-1, 1] input in the direction of the input if price is
    outside the bands (pct_b outside [0, 1]), then clips."""
    return max(-1.0, min(1.0, 1.0 - 2.0 * pct_b))


def _score_ma_cross(label: str | None) -> float:
    return {"golden": 1.0, "dead": -1.0}.get(label, 0.0)


def _volume_confidence(vol_ratio: float | None, weak: float = 0.5, strong: float = 1.5) -> float:
    """Volume doesn't have its own direction -- it scales confidence in
    whatever the other indicators already say. Below-average volume damps
    the score toward 0; above-average volume amplifies it slightly."""
    if vol_ratio is None or pd.isna(vol_ratio):
        return 1.0
    return max(weak, min(strong, vol_ratio))


def compute_quant_score(indicator_row: pd.Series, weights: ScoringWeights = DEFAULT_WEIGHTS) -> float:
    """Takes one row from sw1.indicators.technical.compute_all_technical_indicators
    output (must have RSI, MACD_HIST, BB_PCT_B, MA_CROSS, VOL_RATIO) and
    returns a single quant score in [-1, 1]."""
    sub_scores = {
        "rsi": _score_rsi(indicator_row["RSI"]) if pd.notna(indicator_row.get("RSI")) else 0.0,
        "macd": _score_macd(indicator_row["MACD_HIST"]) if pd.notna(indicator_row.get("MACD_HIST")) else 0.0,
        "bollinger": _score_bollinger(indicator_row["BB_PCT_B"]) if pd.notna(indicator_row.get("BB_PCT_B")) else 0.0,
        "ma_cross": _score_ma_cross(indicator_row.get("MA_CROSS")),
    }
    raw = (
        sub_scores["rsi"] * weights.rsi
        + sub_scores["macd"] * weights.macd
        + sub_scores["bollinger"] * weights.bollinger
        + sub_scores["ma_cross"] * weights.ma_cross
    )
    confidence = _volume_confidence(indicator_row.get("VOL_RATIO"))
    return max(-1.0, min(1.0, raw * confidence))


def compute_integrated_score(
    quant_score: float,
    news_score: float,
    weights: ScoringWeights = DEFAULT_WEIGHTS,
) -> float:
    """Blends the technical quant_score with the news sentiment score
    (both expected in [-1, 1]) into one integrated score in [-1, 1]."""
    if not (-1.0 <= quant_score <= 1.0):
        raise ValueError(f"quant_score must be in [-1, 1], got {quant_score}")
    if not (-1.0 <= news_score <= 1.0):
        raise ValueError(f"news_score must be in [-1, 1], got {news_score}")
    blended = quant_score * weights.quant_blend + news_score * weights.news_blend
    return max(-1.0, min(1.0, blended))


@dataclass
class TradeSignal:
    signal: str  # "buy_1" | "buy_2" | "hold" | "caution" | "stop_loss" | "take_profit"
    integrated_score: float | None
    reasons: list[str] = field(default_factory=list)


def classify_signal(
    integrated_score: float,
    price_return_from_entry: float | None = None,
    thresholds: TradeThresholds = DEFAULT_THRESHOLDS,
) -> TradeSignal:
    """Turns an integrated score (and, if you're already holding a
    position, the price return since entry) into a trade signal.

    Price-based exits (stop-loss / take-profit) take priority over the
    score-based entries, since capital preservation comes first once a
    position is open.
    """
    reasons: list[str] = []

    if price_return_from_entry is not None:
        if price_return_from_entry <= thresholds.stop_loss_pct:
            reasons.append(f"price return {price_return_from_entry:.1%} <= stop-loss {thresholds.stop_loss_pct:.1%}")
            return TradeSignal("stop_loss", integrated_score, reasons)
        if price_return_from_entry >= thresholds.take_profit_pct:
            reasons.append(f"price return {price_return_from_entry:.1%} >= take-profit {thresholds.take_profit_pct:.1%}")
            return TradeSignal("take_profit", integrated_score, reasons)

    if integrated_score >= thresholds.buy_2_score:
        reasons.append(f"integrated score {integrated_score:.2f} >= 2차 매수 threshold {thresholds.buy_2_score}")
        return TradeSignal("buy_2", integrated_score, reasons)
    if integrated_score >= thresholds.buy_1_score:
        reasons.append(f"integrated score {integrated_score:.2f} >= 1차 매수 threshold {thresholds.buy_1_score}")
        return TradeSignal("buy_1", integrated_score, reasons)
    if integrated_score <= thresholds.caution_score:
        reasons.append(f"integrated score {integrated_score:.2f} <= caution threshold {thresholds.caution_score}")
        return TradeSignal("caution", integrated_score, reasons)

    reasons.append("integrated score within neutral band")
    return TradeSignal("hold", integrated_score, reasons)

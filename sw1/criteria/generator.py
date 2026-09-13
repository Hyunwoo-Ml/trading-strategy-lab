"""
SW1 -- user-parameterized price-level criteria generator.

This is a different (and, per user feedback on 2026-09-13, more accurate to
the original intent) mode from sw1.scoring.integrate: instead of blending
indicators into one abstract [-1, 1] "quant score" and thresholding that,
this module turns a small set of USER-CHOSEN parameters (how much stop-loss
risk to take, what reward:risk ratio to target) plus the existing technical
indicators (sw1.indicators.technical) into concrete, per-ticker DOLLAR PRICE
LEVELS:

    - buy_1_price   -- first-tranche entry (shallower technical support)
    - buy_2_price   -- second-tranche entry (deeper technical support)
    - target_price  -- take-profit level, derived from the user's own
                        stop_loss_pct and reward_risk_ratio (a classic
                        "risk multiple" target: target = entry + risk*R)

A named set of these parameters is a "model" (e.g. "model_1") that gets
exported to SW2 (see sw2/price_criteria_model.py) to actually trade against.
Multiple parameter sets -> multiple models -> compared the same way SW2
already compares the older score-threshold models.

Design note: buy_1/buy_2 are NOT re-derived from a daily abstract score.
They come straight off technical support levels (Bollinger lower band,
50-day MA, N-day rolling low), so they read as "if AAPL pulls back to
about $X, that's your first entry" -- exactly the mental model described
in feedback, rather than a score that changes meaning every day.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd


@dataclass
class PriceCriteriaParams:
    """User-chosen parameters for one price-criteria model. Everything a
    person needs to set by hand lives here; the rest (buy_1/buy_2 support
    levels) is derived from technical indicators in generate_price_criteria().
    """

    model_name: str
    stop_loss_pct: float = -0.08       # user-set; must be negative (e.g. -0.08 = -8%)
    reward_risk_ratio: float = 2.0     # target = entry + (entry - stop_loss_price) * ratio
    buy2_lookback_days: int = 60       # window for the deeper structural support level
    description: str = ""

    def validate(self) -> None:
        if self.stop_loss_pct >= 0:
            raise ValueError(f"stop_loss_pct must be negative, got {self.stop_loss_pct}")
        if self.reward_risk_ratio <= 0:
            raise ValueError(f"reward_risk_ratio must be positive, got {self.reward_risk_ratio}")
        if self.buy2_lookback_days < 2:
            raise ValueError(f"buy2_lookback_days must be >= 2, got {self.buy2_lookback_days}")


@dataclass
class PriceCriteria:
    """One ticker's generated criteria snapshot for one model, on one date."""

    model_name: str
    ticker: str
    date: str
    close: float
    buy_1_price: float
    buy_2_price: float
    stop_loss_pct: float
    target_price: float
    basis: dict = field(default_factory=dict)  # human-readable explanation per level


def _pick_buy_1(close: float, bb_lower: float | None, ma50: float | None) -> tuple[float, str]:
    """Shallower support -- whichever of BB lower band / 50-day MA is closer
    to (but still below) the current price. Falls back to a flat -3% off
    close if neither technical anchor is available yet (e.g. <50 days of
    history)."""
    candidates = [(v, label) for v, label in ((bb_lower, "볼린저 하단(20일)"), (ma50, "50일 이동평균")) if v is not None and pd.notna(v) and v < close]
    if candidates:
        value, label = max(candidates, key=lambda c: c[0])
        return float(value), label
    return close * 0.97, "폴백: 현재가 -3% (지표 데이터 부족)"


def _pick_buy_2(close: float, buy_1_price: float, bb_lower: float | None, lookback_low: float | None, lookback_days: int) -> tuple[float, str]:
    """Deeper support -- whichever of BB lower band / N-day rolling low is
    further below buy_1_price. Falls back to buy_1_price - 5% if neither is
    available or both sit above buy_1_price."""
    candidates = [(v, label) for v, label in ((bb_lower, "볼린저 하단(20일)"), (lookback_low, f"{lookback_days}일 최저가")) if v is not None and pd.notna(v) and v < buy_1_price]
    if candidates:
        value, label = min(candidates, key=lambda c: c[0])
        return float(value), label
    return buy_1_price * 0.95, "폴백: 1차 매수가 -5% (지표 데이터 부족)"


def generate_price_criteria(
    ticker: str,
    indicators_df: pd.DataFrame,
    params: PriceCriteriaParams,
) -> PriceCriteria:
    """indicators_df must be the output of
    sw1.indicators.technical.compute_all_technical_indicators (needs Close,
    BB_LOWER, MA_50 columns at minimum -- MA_50/BB_LOWER can be NaN for
    tickers with short history; this degrades gracefully via the fallbacks
    above)."""
    params.validate()
    if len(indicators_df) == 0:
        raise ValueError("indicators_df must not be empty")

    last = indicators_df.iloc[-1]
    close = float(last["Close"])
    bb_lower = last.get("BB_LOWER")
    ma50 = last.get("MA_50")

    buy_1_price, buy_1_basis = _pick_buy_1(close, bb_lower, ma50)

    lookback_low = indicators_df["Close"].tail(params.buy2_lookback_days).min()
    buy_2_price, buy_2_basis = _pick_buy_2(close, buy_1_price, bb_lower, lookback_low, params.buy2_lookback_days)

    stop_loss_price = buy_1_price * (1 + params.stop_loss_pct)
    risk_per_share = buy_1_price - stop_loss_price
    target_price = buy_1_price + risk_per_share * params.reward_risk_ratio

    date_value = indicators_df.index[-1]
    date_str = date_value.date().isoformat() if hasattr(date_value, "date") else str(date_value)

    return PriceCriteria(
        model_name=params.model_name,
        ticker=ticker,
        date=date_str,
        close=close,
        buy_1_price=round(buy_1_price, 2),
        buy_2_price=round(buy_2_price, 2),
        stop_loss_pct=params.stop_loss_pct,
        target_price=round(target_price, 2),
        basis={
            "buy_1": buy_1_basis,
            "buy_2": buy_2_basis,
            "target": f"손절가 대비 리스크 x{params.reward_risk_ratio} (stop_loss_pct={params.stop_loss_pct:.1%})",
        },
    )


def load_price_criteria_params(config_dir: Path) -> list[PriceCriteriaParams]:
    """Loads every *.json file in config_dir as a PriceCriteriaParams. This
    is the "user input" surface -- editing/adding a JSON file here (and
    committing it) is how a person defines a new model without touching any
    code, per the GitHub-Pages-is-static / no-live-backend constraint."""
    config_dir = Path(config_dir)
    if not config_dir.exists():
        return []

    params_list = []
    for path in sorted(config_dir.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        params = PriceCriteriaParams(
            model_name=raw["model_name"],
            stop_loss_pct=raw.get("stop_loss_pct", -0.08),
            reward_risk_ratio=raw.get("reward_risk_ratio", 2.0),
            buy2_lookback_days=raw.get("buy2_lookback_days", 60),
            description=raw.get("description", ""),
        )
        params.validate()
        params_list.append(params)
    return params_list

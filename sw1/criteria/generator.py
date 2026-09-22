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

    # -- 2026-09-13 feedback: market-wide/volume/weekly/event confirmation --
    # These gate *new entries* only (see sw2.price_criteria_model.MarketContext);
    # they never touch buy_1/buy_2/target_price generation below, so a model's
    # support levels stay comparable across time even as confirmation rules
    # tighten or loosen.
    min_volume_ratio: float = 1.0      # require Volume >= this x N-day avg (VOL_RATIO) to treat a touch as real buying interest
    require_weekly_uptrend: bool = False  # if True, also block entries when weekly trend is merely "flat" (not just "down")

    # -- 2026-09-14 feedback: 사용자가 수기 입력한 시황/뉴스(sw1/news/input/
    # {TICKER}.txt)도 이 가격기준모델의 진입 판단에 반영되어야 한다는 요청.
    # min_volume_ratio/require_weekly_uptrend와 같은 자리(신규 진입 게이트)에
    # 놓는다 -- 이미 보유 중인 포지션의 buy_1/buy_2/target_price 자체는 절대
    # 건드리지 않고, "오늘 새로 진입할지"만 이 값으로 보류시킨다. None(기본값)
    # 이면 이 게이트는 완전히 꺼진 상태 -- 기존 모델(min_news_score를 아직
    # 설정 안 한 설정 파일)은 이 필드가 생기기 전과 정확히 동일하게 동작한다.
    min_news_score: float | None = None  # 오늘 해당 티커의 news_score가 이 값보다 낮으면 신규 진입(buy_1/buy_2) 보류

    def validate(self) -> None:
        if self.stop_loss_pct >= 0:
            raise ValueError(f"stop_loss_pct must be negative, got {self.stop_loss_pct}")
        if self.reward_risk_ratio <= 0:
            raise ValueError(f"reward_risk_ratio must be positive, got {self.reward_risk_ratio}")
        if self.min_news_score is not None and not (-1.0 <= self.min_news_score <= 1.0):
            raise ValueError(f"min_news_score must be in [-1, 1] or None, got {self.min_news_score}")
        if self.buy2_lookback_days < 2:
            raise ValueError(f"buy2_lookback_days must be >= 2, got {self.buy2_lookback_days}")
        if self.min_volume_ratio < 0:
            raise ValueError(f"min_volume_ratio must be >= 0, got {self.min_volume_ratio}")


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
    above).

    2026-09-22 (1-day lag fix): buy_1_price/buy_2_price used to be derived
    from the SAME row (today's) whose close they were then compared
    against in sw2.price_criteria_model.PriceCriteriaModel.decide() (and
    in the live pipeline, scripts/run_daily_paper_trading.py reading the
    same day's latest.csv). Because _pick_buy_1/_pick_buy_2 only accept a
    candidate support level that's strictly below the reference close,
    anchoring both to today's own close made buy_1_price mathematically
    always < today's close (or hit the -3% fallback) -- so
    `criteria.close <= criteria.buy_1_price` could essentially never be
    true on the day it was computed. Confirmed empirically against 77 rows
    of live production data (data/sw1/price_criteria/price_model_1|2/
    history.csv): zero touches, ever.

    Fix: when at least 2 rows of history are available, support levels
    (bb_lower/ma50/lookback_low, and the "close" used inside
    _pick_buy_1/_pick_buy_2's `v < close` filters) are computed from
    YESTERDAY's row (indicators_df.iloc[-2]) -- i.e. "if the stock pulls
    back to about $X (derived from data through yesterday), that's your
    entry" -- while the `close` field stored on the returned PriceCriteria
    (the value actually compared against buy_1_price/buy_2_price by
    decide()) stays TODAY's actual close. With fewer than 2 rows (e.g. a
    ticker's very first day of history), there is no "yesterday" to
    anchor to, so this falls back to the old same-day behavior --
    unavoidable on day 1, and _pick_buy_1/_pick_buy_2's fallback branches
    already handle the degenerate case gracefully."""
    params.validate()
    if len(indicators_df) == 0:
        raise ValueError("indicators_df must not be empty")

    today = indicators_df.iloc[-1]
    close = float(today["Close"])

    has_reference_day = len(indicators_df) >= 2
    reference = indicators_df.iloc[-2] if has_reference_day else today
    reference_close = float(reference["Close"])
    bb_lower = reference.get("BB_LOWER")
    ma50 = reference.get("MA_50")

    reference_history = indicators_df.iloc[:-1] if has_reference_day else indicators_df
    lookback_low = reference_history["Close"].tail(params.buy2_lookback_days).min()

    buy_1_price, buy_1_basis = _pick_buy_1(reference_close, bb_lower, ma50)
    buy_2_price, buy_2_basis = _pick_buy_2(reference_close, buy_1_price, bb_lower, lookback_low, params.buy2_lookback_days)

    if has_reference_day:
        buy_1_basis = f"{buy_1_basis} (전일 종가 기준)"
        buy_2_basis = f"{buy_2_basis} (전일 종가 기준)"

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
            min_volume_ratio=raw.get("min_volume_ratio", 1.0),
            require_weekly_uptrend=raw.get("require_weekly_uptrend", False),
            min_news_score=raw.get("min_news_score"),
        )
        params.validate()
        params_list.append(params)
    return params_list

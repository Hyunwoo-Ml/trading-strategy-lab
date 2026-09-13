"""
SW2 -- decision logic for "price-criteria" models (see sw1/criteria/generator.py).

Unlike sw2.models.TradingModel (which thresholds an abstract [-1, 1] score
every day), a PriceCriteriaModel trades off concrete dollar price levels
that SW1 exports and refreshes daily: buy at (or below) buy_1_price, add a
second tranche at buy_2_price, exit at stop_loss_pct below entry or once
target_price is reached. If none of those conditions are met on a given
day, it deliberately does nothing -- it does not trade just because a day
has passed.

2026-09-13 update: a *new* entry (buy_1 or buy_2) can additionally be
gated by real-time confirmation signals via MarketContext -- volume
confirmation, weekly-timeframe trend, market-wide regime, and a macro/
earnings event blackout window. These only ever turn a would-be buy into
a "hold"; they never touch stop_loss/take_profit exits (capital
preservation always takes priority over waiting for a cleaner setup) and
they never change SW1's underlying buy_1_price/buy_2_price/target_price
levels. Every field on MarketContext defaults to None/False so existing
callers that don't pass one behave exactly as before.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sw1.criteria.generator import PriceCriteria, PriceCriteriaParams
from sw1.scoring.integrate import TradeSignal


@dataclass
class MarketContext:
    """Optional real-time confirmation signals for a *new* entry. All
    fields are optional/default-off so a caller that skips this argument
    entirely gets the pre-2026-09-13 behavior unchanged."""

    volume_ratio: float | None = None       # today's Volume / N-day avg (sw1.indicators.technical VOL_RATIO)
    weekly_trend: str | None = None         # "up" | "down" | "flat" (sw1.indicators.weekly.compute_weekly_trend)
    market_regime: str | None = None        # "risk_on" | "risk_off" (sw1.market.regime.compute_market_regime)
    event_blackout: bool = False            # sw1.calendar.events.is_event_blackout(...).is_blackout
    event_reasons: list[str] = field(default_factory=list)


@dataclass
class PriceCriteriaModel:
    """One registered price-criteria model. Wraps a PriceCriteriaParams
    (the user-chosen risk parameters); the actual buy_1/buy_2/target levels
    come in fresh each day via the `criteria` argument to decide(), since
    SW1 regenerates them from the latest technical data."""

    params: PriceCriteriaParams

    @property
    def name(self) -> str:
        return self.params.model_name

    def _entry_block_reason(self, ctx: MarketContext) -> str | None:
        """Checked identically for buy_1 and buy_2 -- adding to a losing
        thesis (buy_2) during a confirmed weekly downtrend, a risk-off
        market, or right before a macro/earnings event is exactly as
        unwanted as a fresh buy_1 under the same conditions."""
        if ctx.event_blackout:
            return "; ".join(ctx.event_reasons) or "이벤트 블랙아웃 기간"
        if ctx.market_regime == "risk_off":
            return "시장 전체 리스크오프 국면 (QQQ 하락추세 확인)"
        if ctx.weekly_trend == "down":
            return "주봉 추세 하락 확인 -- 신규 진입 보류"
        if self.params.require_weekly_uptrend and ctx.weekly_trend == "flat":
            return "주봉 추세가 상승 확인 전(횡보) -- 이 모델은 주봉 상승 확인을 요구함"
        if ctx.volume_ratio is not None and ctx.volume_ratio < self.params.min_volume_ratio:
            return (
                f"거래량 부족 (평균 대비 {ctx.volume_ratio:.2f}배 < 기준 "
                f"{self.params.min_volume_ratio:.2f}배) -- 실제 매수세 확인 안 됨"
            )
        return None

    def decide(
        self,
        criteria: PriceCriteria,
        is_holding: bool,
        tranche_count: int,
        price_return_from_entry: float | None,
        market_context: MarketContext | None = None,
    ) -> TradeSignal:
        """Price-based exit takes priority over new entries, same as the
        score-based models -- capital preservation first. Exits are never
        gated by market_context; only new entries (buy_1/buy_2) are."""
        reasons: list[str] = []

        if is_holding and price_return_from_entry is not None:
            if price_return_from_entry <= criteria.stop_loss_pct:
                reasons.append(
                    f"price return {price_return_from_entry:.1%} <= stop-loss {criteria.stop_loss_pct:.1%}"
                )
                return TradeSignal("stop_loss", None, reasons)
            if criteria.close >= criteria.target_price:
                reasons.append(f"close {criteria.close:.2f} >= target {criteria.target_price:.2f}")
                return TradeSignal("take_profit", None, reasons)

        block_reason = self._entry_block_reason(market_context) if market_context is not None else None

        if not is_holding and criteria.close <= criteria.buy_1_price:
            if block_reason:
                reasons.append(
                    f"buy_1 가격조건 충족(close {criteria.close:.2f} <= {criteria.buy_1_price:.2f})이지만 "
                    f"진입 보류: {block_reason}"
                )
                return TradeSignal("hold", None, reasons)
            reasons.append(f"close {criteria.close:.2f} <= buy_1 {criteria.buy_1_price:.2f} ({criteria.basis.get('buy_1')})")
            return TradeSignal("buy_1", None, reasons)

        if is_holding and tranche_count < 2 and criteria.close <= criteria.buy_2_price:
            if block_reason:
                reasons.append(
                    f"buy_2 가격조건 충족(close {criteria.close:.2f} <= {criteria.buy_2_price:.2f})이지만 "
                    f"진입 보류: {block_reason}"
                )
                return TradeSignal("hold", None, reasons)
            reasons.append(f"close {criteria.close:.2f} <= buy_2 {criteria.buy_2_price:.2f} ({criteria.basis.get('buy_2')})")
            return TradeSignal("buy_2", None, reasons)

        reasons.append("no price level reached today")
        return TradeSignal("hold", None, reasons)

"""
SW2 -- decision logic for "price-criteria" models (see sw1/criteria/generator.py).

Unlike sw2.models.TradingModel (which thresholds an abstract [-1, 1] score
every day), a PriceCriteriaModel trades off concrete dollar price levels
that SW1 exports and refreshes daily: buy at (or below) buy_1_price, add a
second tranche at buy_2_price, exit at stop_loss_pct below entry or once
target_price is reached. If none of those conditions are met on a given
day, it deliberately does nothing -- it does not trade just because a day
has passed.
"""
from __future__ import annotations

from dataclasses import dataclass

from sw1.criteria.generator import PriceCriteria, PriceCriteriaParams
from sw1.scoring.integrate import TradeSignal


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

    def decide(
        self,
        criteria: PriceCriteria,
        is_holding: bool,
        tranche_count: int,
        price_return_from_entry: float | None,
    ) -> TradeSignal:
        """Price-based exit takes priority over new entries, same as the
        score-based models -- capital preservation first."""
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

        if not is_holding and criteria.close <= criteria.buy_1_price:
            reasons.append(f"close {criteria.close:.2f} <= buy_1 {criteria.buy_1_price:.2f} ({criteria.basis.get('buy_1')})")
            return TradeSignal("buy_1", None, reasons)

        if is_holding and tranche_count < 2 and criteria.close <= criteria.buy_2_price:
            reasons.append(f"close {criteria.close:.2f} <= buy_2 {criteria.buy_2_price:.2f} ({criteria.basis.get('buy_2')})")
            return TradeSignal("buy_2", None, reasons)

        reasons.append("no price level reached today")
        return TradeSignal("hold", None, reasons)

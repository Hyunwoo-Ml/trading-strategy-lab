"""
SW2 -- trading-rule "model" registry.

A "model" here is just a named, reproducible configuration for turning
integrated scores into trades -- e.g. sw1's default ScoringWeights/
TradeThresholds is one model, but you might register a "technical-only"
model that ignores news, an "aggressive" model with lower buy thresholds,
etc. SW2's whole point (per the original 자소서) is running several of
these side by side and comparing their Daily 수익률 with statistical rigor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from sw1.scoring.integrate import (
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    ScoringWeights,
    TradeThresholds,
    TradeSignal,
    classify_signal,
    compute_integrated_score,
)


@dataclass
class TradingModel:
    """One registered strategy configuration. `signal_fn` is the actual
    decision function -- defaults to sw1's classify_signal wired up with
    this model's own weights/thresholds, but can be overridden entirely
    for a model that doesn't use the quant/news blend at all."""

    name: str
    weights: ScoringWeights = field(default_factory=lambda: DEFAULT_WEIGHTS)
    thresholds: TradeThresholds = field(default_factory=lambda: DEFAULT_THRESHOLDS)
    description: str = ""
    signal_fn: Callable[[float, float | None], TradeSignal] | None = None

    def __post_init__(self) -> None:
        self.weights.validate()

    def decide(
        self,
        quant_score: float,
        news_score: float = 0.0,
        price_return_from_entry: float | None = None,
    ) -> TradeSignal:
        if self.signal_fn is not None:
            return self.signal_fn(quant_score, price_return_from_entry)
        integrated = compute_integrated_score(quant_score, news_score, weights=self.weights)
        return classify_signal(integrated, price_return_from_entry, thresholds=self.thresholds)


class ModelRegistry:
    """Keeps registered models by name. Deliberately dict-backed and tiny --
    SW2's persistence layer (sw2/ledger.py) references models by name string,
    not by object identity, so this registry just needs to not collide names."""

    def __init__(self) -> None:
        self._models: dict[str, TradingModel] = {}

    def register(self, model: TradingModel) -> None:
        if model.name in self._models:
            raise ValueError(f"a model named {model.name!r} is already registered")
        self._models[model.name] = model

    def get(self, name: str) -> TradingModel:
        try:
            return self._models[name]
        except KeyError:
            raise KeyError(f"no model registered under {name!r}; known: {sorted(self._models)}") from None

    def all(self) -> list[TradingModel]:
        return list(self._models.values())

    def names(self) -> list[str]:
        return list(self._models.keys())


def default_registry() -> ModelRegistry:
    """A starter set of models to compare -- the baseline (sw1 defaults),
    a technical-only variant (news weight zeroed out), and a conservative
    variant (higher buy thresholds, tighter stop-loss). Feel free to
    register more; this is just enough to prove the comparison pipeline
    works end-to-end."""
    registry = ModelRegistry()

    registry.register(
        TradingModel(
            name="baseline",
            weights=DEFAULT_WEIGHTS,
            thresholds=DEFAULT_THRESHOLDS,
            description="sw1 기본 가중치/임계값 그대로 사용하는 기준 모델",
        )
    )

    registry.register(
        TradingModel(
            name="technical_only",
            weights=ScoringWeights(quant_blend=1.0, news_blend=0.0),
            thresholds=DEFAULT_THRESHOLDS,
            description="뉴스 감성 점수를 완전히 배제하고 기술적 지표만으로 판단",
        )
    )

    registry.register(
        TradingModel(
            name="conservative",
            weights=DEFAULT_WEIGHTS,
            thresholds=TradeThresholds(
                buy_1_score=0.45,
                buy_2_score=0.75,
                caution_score=-0.20,
                stop_loss_pct=-0.05,
                take_profit_pct=0.12,
            ),
            description="더 높은 매수 임계값과 더 타이트한 손절선을 쓰는 보수적 모델",
        )
    )

    return registry


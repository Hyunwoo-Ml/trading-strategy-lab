"""
SW2 -- risk-based position sizing (Task #22).

v1 sizing (the old TRANCHE_FRACTION constant in
scripts/run_daily_paper_trading.py) put a fixed 5% of starting cash into
every buy tranche, regardless of how tight or wide that model/ticker's
stop-loss was. That means a model with a tight -3% stop and a model with a
wide -10% stop risked very different amounts of capital per trade even
though both bought the same dollar amount -- the classic weakness of
fixed-fraction sizing.

This module sizes each tranche instead so that IF the stop-loss actually
triggers, the position loses roughly a fixed fraction of starting cash
(RISK_FRACTION), regardless of how tight or wide that particular stop is:
a tighter stop can afford a bigger position for the same dollar risk, a
wider stop needs a smaller one. The result is clamped to
[MIN_TRANCHE_FRACTION, MAX_TRANCHE_FRACTION] of starting cash so an
extreme stop (near-zero or very wide) can never produce a nonsensical
tranche size.

This governs the DOLLAR AMOUNT passed into Portfolio.buy() -- it doesn't
touch sw2/ledger.py, which stays sizing-agnostic (buy() just clips whatever
dollar amount it's given to available cash; see its own docstring).

2026-09-22 retune (benchmark-gap investigation, user request): the original
v1-matching constants below (RISK_FRACTION=0.004, bounds [0.02, 0.12]) sized
every tranche at only ~5-8% of starting cash even at max tranche_count=2 per
ticker, across the M7 universe. Cross-referencing scripts/run_historical_
backtest.py's data/backtest/results.json showed this was the dominant,
model-agnostic cause of the ~3-year gap against the SPY/QQQ buy-and-hold
benchmarks (which are 100%-invested from day one): every one of the five
registered models was structurally capped well under full deployment, in a
period where SPY/QQQ were up 86%/111%. Confirming evidence: WITHIN the five
models, the ones whose stop_loss_pct happened to produce a bigger tranche
fraction under the old constants (conservative and price_model_2, both
-5% stops -> 8%) outperformed their otherwise-similar siblings with wider
stops and smaller tranches (baseline -7% -> 5.7%; price_model_1 -8% -> 5%)
-- i.e. more capital deployed per signal correlated directly with better
total_return across the board, independent of each model's entry/exit
logic. Doubling RISK_FRACTION (and raising the bounds proportionally so the
wider range isn't immediately clamped away) doubles typical tranche size
for every model uniformly, without touching any model's own thresholds/
weights/entry logic -- a structural sizing fix rather than a signal-quality
change. Verify by re-running the historical-backtest workflow after this
change lands and comparing data/backtest/results.json total_return against
the pre-change numbers recorded in TASKS.md.
"""
from __future__ import annotations

RISK_FRACTION = 0.008         # risk ~0.8% of starting cash per tranche if the stop-loss triggers --
                               # 2x the original v1-matching value (see 2026-09-22 retune note above).
                               # A typical ~8% stop now sizes a tranche at ~10% of starting cash
                               # (0.008 / 0.08 = 0.10), a ~5% stop at ~16% (0.008 / 0.05 = 0.16).
MIN_TRANCHE_FRACTION = 0.03   # never size a tranche below 3% of starting cash
MAX_TRANCHE_FRACTION = 0.20   # never size a tranche above 20% of starting cash


def risk_based_tranche_dollars(
    starting_cash: float,
    stop_loss_pct: float,
    risk_fraction: float = RISK_FRACTION,
    min_fraction: float = MIN_TRANCHE_FRACTION,
    max_fraction: float = MAX_TRANCHE_FRACTION,
) -> float:
    """Dollar amount for one buy tranche, sized so a stop-loss trigger loses
    approximately `risk_fraction` of `starting_cash`.

    `stop_loss_pct` is the price-return-from-entry threshold that triggers a
    stop-loss, expressed as a negative fraction (e.g. -0.08 for -8%) -- the
    convention used throughout sw1/sw2 (TradeThresholds.stop_loss_pct,
    PriceCriteria.stop_loss_pct). Only its magnitude ("stop distance")
    matters here, so a positive value works the same as its negative.

    fraction = risk_fraction / stop_distance, clamped to
    [min_fraction, max_fraction]:
      - a tighter stop (smaller stop_distance) -> a larger fraction (bigger
        tranche), up to max_fraction.
      - a wider stop (larger stop_distance) -> a smaller fraction (smaller
        tranche), down to min_fraction.

    Falls back to the midpoint of [min_fraction, max_fraction] when
    stop_loss_pct is 0 or missing (no usable stop distance to size
    against), rather than dividing by zero or raising -- same
    graceful-degradation pattern as the rest of this pipeline.
    """
    stop_distance = abs(stop_loss_pct)
    if stop_distance <= 0:
        fraction = (min_fraction + max_fraction) / 2
    else:
        fraction = risk_fraction / stop_distance
        fraction = max(min_fraction, min(max_fraction, fraction))
    return starting_cash * fraction

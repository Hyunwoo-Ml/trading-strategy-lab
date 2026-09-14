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
"""
from __future__ import annotations

RISK_FRACTION = 0.004         # risk ~0.4% of starting cash per tranche if the stop-loss triggers --
                               # tuned so a typical ~8% stop (SW1's price-criteria default) reproduces
                               # the old v1 fixed 5% tranche almost exactly (0.004 / 0.08 = 0.05).
MIN_TRANCHE_FRACTION = 0.02   # never size a tranche below 2% of starting cash
MAX_TRANCHE_FRACTION = 0.12   # never size a tranche above 12% of starting cash


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

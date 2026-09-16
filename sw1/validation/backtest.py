"""
SW2 -- long-horizon historical backtest period bucketing.

This is the pure-math half of the "3-year historical backtest, broken down
by period" feature (2026-09-16 user request): given a model's equity curve
(the same shape sw2.ledger.Portfolio.equity_df() produces), split it into
calendar periods (quarter by default, but month/year both work the same
way) and report each period's return.

Deliberately separate from sw1.validation.walkforward, which answers a
different question -- whether the quant_score FORMULA has genuine
predictive power (see that module's docstring). This module instead
buckets an already-simulated, already-traded EQUITY CURVE (real buy/sell/
stop-loss rules, position sizing, transaction costs all already applied)
into periods so "how did each model actually do, quarter by quarter" has a
direct, intuitive number instead of one single 3-year lump sum.

Kept free of network/file I/O (like sw1.validation.walkforward) so it's
fully unit-testable against synthetic equity curves -- the actual 3-year
trading simulation lives in scripts/run_historical_backtest.py.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass
class PeriodReturn:
    period: str  # e.g. "2024Q1" (quarterly), "2024-03" (monthly), "2024" (yearly)
    start_date: str
    end_date: str
    start_equity: float
    end_equity: float
    period_return: float | None  # None only if start_equity is 0 (division undefined)

    def to_dict(self) -> dict:
        return {
            "period": self.period,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "start_equity": self.start_equity,
            "end_equity": self.end_equity,
            "period_return": self.period_return,
        }


def bucket_equity_by_period(equity_df: pd.DataFrame, freq: str = "Q") -> list[PeriodReturn]:
    """Splits an equity curve into calendar periods and reports each
    period's return.

    `equity_df` must have a DatetimeIndex and an "equity" column (exactly
    sw2.ledger.Portfolio.equity_df()'s shape). `freq` is a pandas period
    frequency alias -- "Q" (quarterly, the default), "M" (monthly), or "Y"
    (yearly) are the ones this feature actually uses.

    Each period's return is computed against the PREVIOUS period's closing
    equity (a running chain: period 2 starts where period 1 ended), not
    against the very first day of the whole series -- that's what makes
    each entry a genuine "how did this quarter go" number rather than a
    reslice of the same cumulative total every time. The very first period
    has no prior period to chain from, so it's measured from its own first
    observation instead (this slightly understates the first period if
    trading had already produced gains/losses before that period's first
    data point, but there is no earlier equity value to compare against).

    Returns one PeriodReturn per period that has at least one observation
    in `equity_df` -- periods with no trading-day data at all (e.g. before
    the series starts) are simply absent, not filled with nulls.
    """
    if equity_df.empty:
        return []

    df = equity_df.sort_index()
    periods = df.index.to_period(freq)

    results: list[PeriodReturn] = []
    prev_end_equity: float | None = None
    prev_end_date: str | None = None

    for period in periods.unique():
        mask = periods == period
        group = df.loc[mask]
        start_equity = prev_end_equity if prev_end_equity is not None else float(group["equity"].iloc[0])
        start_date = prev_end_date if prev_end_date is not None else group.index[0].date().isoformat()
        end_equity = float(group["equity"].iloc[-1])
        end_date = group.index[-1].date().isoformat()
        period_return = (end_equity / start_equity - 1.0) if start_equity else None

        results.append(
            PeriodReturn(
                period=str(period),
                start_date=start_date,
                end_date=end_date,
                start_equity=start_equity,
                end_equity=end_equity,
                period_return=period_return,
            )
        )
        prev_end_equity = end_equity
        prev_end_date = end_date

    return results


def total_return(equity_df: pd.DataFrame) -> float | None:
    """Return over the whole series: last equity vs. first equity. None if
    the series is empty or starts at 0 (division undefined)."""
    if equity_df.empty:
        return None
    df = equity_df.sort_index()
    start_equity = float(df["equity"].iloc[0])
    end_equity = float(df["equity"].iloc[-1])
    if start_equity == 0:
        return None
    return end_equity / start_equity - 1.0

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


@dataclass
class DrawdownResult:
    """The single worst peak-to-trough decline in an equity curve (Max
    Drawdown / MDD) -- 2026-09-22 follow-up to the RISK_FRACTION 2x sizing
    change (see TASKS.md): that change was known to make any stop-loss hit
    bigger in dollar terms (same loss PERCENTAGE of starting cash, but a
    bigger overall equity swing since positions are now larger), and MDD is
    the standard way to make "how much bigger" a concrete, comparable
    number instead of a hand-wave. Deliberately kept in this module (not a
    new one) since it answers the same kind of question as
    bucket_equity_by_period/total_return -- summarizing an
    already-simulated equity curve -- and takes the identical input shape.
    """

    max_drawdown_pct: float | None  # e.g. -0.23 == a 23% peak-to-trough decline. None only if equity_df is empty.
    peak_date: str | None
    trough_date: str | None
    peak_equity: float | None
    trough_equity: float | None

    def to_dict(self) -> dict:
        return {
            "max_drawdown_pct": self.max_drawdown_pct,
            "peak_date": self.peak_date,
            "trough_date": self.trough_date,
            "peak_equity": self.peak_equity,
            "trough_equity": self.trough_equity,
        }


def max_drawdown(equity_df: pd.DataFrame) -> DrawdownResult:
    """Computes the maximum drawdown over the whole equity curve: the
    largest percentage decline from any running peak to a subsequent
    trough (not just the final value vs. the all-time high -- a curve that
    recovers after its worst decline still reports that earlier, deeper
    decline, since that's the actual worst-case swing an investor living
    through the whole period would have felt).

    `equity_df` must have a DatetimeIndex and an "equity" column (the same
    shape sw2.ledger.Portfolio.equity_df() produces, and the same shape
    bucket_equity_by_period/total_return already take).

    A flat or monotonically-increasing curve has a max drawdown of exactly
    0.0 (never negative) -- the running peak never gets outrun by a lower
    equity value. `peak_date` is the actual day the running peak used for
    the worst decline was SET (the last day at-or-before the trough that
    equity actually equaled that peak value), not merely the first day the
    curve happened to be at-or-above it.

    Returns a DrawdownResult with every field None if `equity_df` is empty.
    """
    if equity_df.empty:
        return DrawdownResult(
            max_drawdown_pct=None, peak_date=None, trough_date=None, peak_equity=None, trough_equity=None
        )

    df = equity_df.sort_index()
    equity = df["equity"].astype(float)
    running_max = equity.cummax()

    # drawdown[i] = how far equity[i] sits below the highest equity seen up
    # to and including day i, as a (non-positive) fraction of that peak.
    # Guarded against a zero-or-negative running peak (no realistic
    # portfolio starts there, but dividing by it would raise/±inf instead
    # of degrading gracefully like the rest of this module does).
    drawdown = pd.Series(
        [(e / rm - 1.0) if rm > 0 else 0.0 for e, rm in zip(equity, running_max)],
        index=equity.index,
    )

    trough_idx = drawdown.idxmin()
    trough_equity = float(equity.loc[trough_idx])
    max_dd = float(drawdown.loc[trough_idx])
    peak_equity = float(running_max.loc[trough_idx])

    # The running peak can hold the same value across many days before a
    # new high is set -- walk back from the trough to the LAST day equity
    # actually equaled that peak (exact match is safe here: peak_equity was
    # read directly off some prior equity value via cummax, never
    # recomputed by arithmetic, so it compares bit-for-bit equal).
    equity_up_to_trough = equity.loc[:trough_idx]
    peak_idx = equity_up_to_trough[equity_up_to_trough == peak_equity].index[-1]

    def _date_str(idx) -> str:
        return idx.date().isoformat() if hasattr(idx, "date") else str(idx)

    return DrawdownResult(
        max_drawdown_pct=max_dd,
        peak_date=_date_str(peak_idx),
        trough_date=_date_str(trough_idx),
        peak_equity=peak_equity,
        trough_equity=trough_equity,
    )

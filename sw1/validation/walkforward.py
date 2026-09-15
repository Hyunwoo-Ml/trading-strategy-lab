"""
SW1 -- walk-forward (rolling-window) validation of the quant scoring logic.

Validates whether sw1.scoring.integrate.compute_quant_score -- a fixed-weight,
hand-tuned formula, NOT a fitted model (see that module's docstring) -- has
genuine, PERIOD-STABLE predictive power for forward returns, rather than an
edge that only shows up in one cherry-picked stretch of history.

There's no "training" step here (the formula's weights are fixed ahead of
time), so this isn't classic train/test walk-forward optimization. Instead
it's a rolling STABILITY check: split the available price history into many
rolling windows, compute the score-vs-forward-return rank correlation
(Spearman IC, "information coefficient") independently within each window,
and look at how consistent that correlation is across windows. A formula
that only "works" in one window and flips sign in most others is a sign of
curve-fitting / noise, not a real signal -- that's the overfitting check the
original project plan ("과최적화 방지를 위한 walk-forward 검증") asked for.

Scope note -- news sentiment scoring (sw1.news.scorer) is intentionally NOT
included here: there is no historical archive of daily market commentary to
replay through the LLM scorer, only the live/current commentary logs
(sw1/news/log.py). A historical backtest of the news component isn't
possible without fabricating commentary, which would defeat the point. See
scripts/run_walkforward_validation.py's docstring for how live paper-trading
history is meant to cover that gap later, once enough of it accumulates.

All functions here are pure (no network, no file I/O) so they're fully
unit-testable against synthetic price/score series -- the live data pull
(yfinance) and indicator computation live in
scripts/run_walkforward_validation.py instead, same split as
scripts/collect_daily_data.py vs its own unit-tested helpers.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

MIN_OBS_PER_WINDOW = 10  # a window with fewer trading days than this is too noisy to trust


def compute_forward_returns(close: pd.Series, horizon_days: int) -> pd.Series:
    """Forward simple return over `horizon_days` TRADING days (this expects
    `close` already indexed by trading date, not calendar date -- so
    `horizon_days` skips weekends/holidays for free). The last
    `horizon_days` rows are NaN (no future price yet to compare against)."""
    if horizon_days <= 0:
        raise ValueError(f"horizon_days must be positive, got {horizon_days}")
    return close.shift(-horizon_days) / close - 1.0


def make_rolling_windows(
    dates: list[date], window_days: int, step_days: int
) -> list[tuple[date, date]]:
    """Splits `dates` into rolling CALENDAR-day windows [start, end) of
    length `window_days`, stepped every `step_days`, covering the whole
    range from the earliest to the latest date. The final window is
    clipped to end just past the data's actual last date rather than
    dropped or padded past it, so no trailing data is silently excluded
    and no window extends into dates that don't exist yet."""
    if window_days <= 0 or step_days <= 0:
        raise ValueError("window_days and step_days must both be positive")
    if not dates:
        return []
    uniq = sorted(set(dates))
    first, last = uniq[0], uniq[-1]

    windows: list[tuple[date, date]] = []
    start = first
    while start <= last:
        end = min(start + timedelta(days=window_days), last + timedelta(days=1))
        windows.append((start, end))
        if end > last:
            break
        start = start + timedelta(days=step_days)
    return windows


def window_ic(
    scores: pd.Series, forward_returns: pd.Series, min_obs: int = MIN_OBS_PER_WINDOW
) -> tuple[float | None, float | None, int]:
    """Spearman rank correlation between `scores` and `forward_returns`
    over their shared, non-NaN index (an inner join by index, so
    mismatched/unaligned indices are handled the same as missing data).
    Returns (ic, p_value, n_obs); ic and p_value are None if fewer than
    `min_obs` valid pairs are available, or if the correlation is
    undefined (e.g. one series is constant)."""
    paired = pd.DataFrame({"score": scores, "ret": forward_returns}).dropna()
    n = len(paired)
    if n < min_obs:
        return None, None, n
    ic, p = spearmanr(paired["score"], paired["ret"])
    if np.isnan(ic):
        return None, None, n
    return float(ic), float(p), n


@dataclass
class WindowResult:
    start: date
    end: date
    n_obs: int
    ic: float | None  # Spearman rank IC, score vs forward return; None if too few obs to trust
    p_value: float | None = None

    def to_dict(self) -> dict:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "n_obs": self.n_obs,
            "ic": self.ic,
            "p_value": self.p_value,
        }


def run_walkforward(
    scores: pd.Series,
    forward_returns: pd.Series,
    window_days: int,
    step_days: int,
    min_obs: int = MIN_OBS_PER_WINDOW,
) -> list[WindowResult]:
    """Runs the rolling-window IC check over the full history covered by
    `scores`'s index. `scores` and `forward_returns` must share a
    date-like index (a DatetimeIndex, or one whose elements have a
    `.date()` method)."""
    idx_dates = [d.date() if hasattr(d, "date") else d for d in scores.index]
    windows = make_rolling_windows(idx_dates, window_days, step_days)

    results: list[WindowResult] = []
    for start, end in windows:
        mask = [(d >= start and d < end) for d in idx_dates]
        window_scores = scores[mask]
        window_returns = forward_returns[mask]
        ic, p, n = window_ic(window_scores, window_returns, min_obs=min_obs)
        results.append(WindowResult(start=start, end=end, n_obs=n, ic=ic, p_value=p))
    return results


def summarize_walkforward(results: list[WindowResult]) -> dict:
    """Aggregates per-window IC results into the overfitting-check summary:
    how many windows had enough data to score, how many of those were
    positive vs negative, and the mean/median/best/worst IC across them.

    How to read this: a stable, mostly-positive spread of IC across many
    INDEPENDENT windows is evidence the score's edge isn't a one-window
    fluke. A coin-flip mix of signs (pct_positive near 0.5) or a mean IC
    near 0 is a sign it probably is one -- i.e. the formula looks like it
    "works" only because of how it happens to fit one period, not because
    it captures something real and repeatable."""
    scored = [r for r in results if r.ic is not None]
    if not scored:
        return {
            "n_windows_total": len(results),
            "n_windows_scored": 0,
            "n_windows_skipped_insufficient_data": len(results),
            "mean_ic": None,
            "median_ic": None,
            "pct_positive": None,
            "best_ic": None,
            "worst_ic": None,
        }
    ics = [r.ic for r in scored]
    positive = [ic for ic in ics if ic > 0]
    return {
        "n_windows_total": len(results),
        "n_windows_scored": len(scored),
        "n_windows_skipped_insufficient_data": len(results) - len(scored),
        "mean_ic": float(np.mean(ics)),
        "median_ic": float(np.median(ics)),
        "pct_positive": len(positive) / len(ics),
        "best_ic": float(max(ics)),
        "worst_ic": float(min(ics)),
    }

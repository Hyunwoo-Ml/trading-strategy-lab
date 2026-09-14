"""
SW2 -- statistical comparison of Daily 수익률 across registered models.

This is the "통계적 엄밀함" (statistical rigor) piece from the original
자소서: rather than eyeballing which model's equity curve looks better,
run a proper hypothesis test on the daily-return series.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats


@dataclass
class ComparisonResult:
    model_a: str
    model_b: str
    mean_daily_return_a: float
    mean_daily_return_b: float
    t_statistic: float | None
    p_value: float | None
    n_a: int
    n_b: int

    @property
    def significant_at_5pct(self) -> bool:
        return self.p_value is not None and self.p_value < 0.05


def compare_daily_returns(
    model_a_name: str,
    returns_a: pd.Series,
    model_b_name: str,
    returns_b: pd.Series,
) -> ComparisonResult:
    """Welch's t-test (unequal variance) on two models' daily-return
    series -- doesn't assume equal variance since different rule sets
    can easily end up with very different volatility.

    Early in a model's life (or for any model that simply hasn't traded
    yet) its daily returns are all exactly 0.0 -- zero variance. Welch's
    t-statistic is then a 0/0 division and scipy returns nan/nan. That is
    a genuinely undefined test outcome, not "no difference detected", so
    it's reported as t_statistic=None/p_value=None rather than a raw NaN.
    This also matters for the JSON this feeds (data/sw2/comparisons/
    latest.json): Python's json module happily writes a bare `NaN`
    token, which is invalid JSON and breaks the dashboard's JSON.parse
    -- None serializes as `null`, which every JSON parser accepts."""
    a = returns_a.dropna().to_numpy()
    b = returns_b.dropna().to_numpy()
    if len(a) < 2 or len(b) < 2:
        raise ValueError("need at least 2 daily-return observations per model to compare")

    t_stat, p_value = stats.ttest_ind(a, b, equal_var=False)
    t_stat_out = None if np.isnan(t_stat) else float(t_stat)
    p_value_out = None if np.isnan(p_value) else float(p_value)

    return ComparisonResult(
        model_a=model_a_name,
        model_b=model_b_name,
        mean_daily_return_a=float(np.mean(a)),
        mean_daily_return_b=float(np.mean(b)),
        t_statistic=t_stat_out,
        p_value=p_value_out,
        n_a=len(a),
        n_b=len(b),
    )


def compare_all_pairs(returns_by_model: dict[str, pd.Series]) -> list[ComparisonResult]:
    """Pairwise comparison across every registered model with enough
    history -- this is what a leaderboard / summary report reads from.
    Pairs without enough history yet are silently skipped rather than
    raising, since early in the project most pairs won't have 2+ days."""
    names = list(returns_by_model.keys())
    results: list[ComparisonResult] = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a_name, b_name = names[i], names[j]
            try:
                results.append(
                    compare_daily_returns(a_name, returns_by_model[a_name], b_name, returns_by_model[b_name])
                )
            except ValueError:
                continue
    return results

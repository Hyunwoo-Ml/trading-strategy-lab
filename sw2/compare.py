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
    t_statistic: float
    p_value: float
    n_a: int
    n_b: int

    @property
    def significant_at_5pct(self) -> bool:
        return self.p_value < 0.05


def compare_daily_returns(
    model_a_name: str,
    returns_a: pd.Series,
    model_b_name: str,
    returns_b: pd.Series,
) -> ComparisonResult:
    """Welch's t-test (unequal variance) on two models' daily-return
    series -- doesn't assume equal variance since different rule sets
    can easily end up with very different volatility."""
    a = returns_a.dropna().to_numpy()
    b = returns_b.dropna().to_numpy()
    if len(a) < 2 or len(b) < 2:
        raise ValueError("need at least 2 daily-return observations per model to compare")

    t_stat, p_value = stats.ttest_ind(a, b, equal_var=False)

    return ComparisonResult(
        model_a=model_a_name,
        model_b=model_b_name,
        mean_daily_return_a=float(np.mean(a)),
        mean_daily_return_b=float(np.mean(b)),
        t_statistic=float(t_stat),
        p_value=float(p_value),
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


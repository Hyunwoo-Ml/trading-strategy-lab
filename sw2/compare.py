"""
SW2 -- statistical comparison of Daily 수익률 across registered models.

This is the "통계적 엄밀함" (statistical rigor) piece from the original
자소서: rather than eyeballing which model's equity curve looks better,
run a proper hypothesis test on the daily-return series.

Task #23 (2026-09-14) extended the original single-pair Welch's t-test with
three things a naive pairwise-comparison setup is missing:

1. Multiple-comparison correction (adjust_for_multiple_comparisons). With N
   registered models, compare_all_pairs runs N*(N-1)/2 independent
   hypothesis tests -- treating each pair's raw p_value < 0.05 as
   "significant" on its own inflates the overall false-positive rate as N
   grows. compare_all_pairs now applies this correction to the whole batch
   by default.
2. Effect size (ComparisonResult.cohens_d). A t-statistic (and its
   p-value) grows with sample size even for a tiny true difference in
   means, so "significant" alone doesn't say whether a difference is
   large enough to matter. Cohen's d standardizes the mean difference by
   the pooled standard deviation, giving a sample-size-independent sense
   of magnitude.
3. Autocorrelation caveat (ComparisonResult.autocorrelation_warning_a/b).
   Welch's t-test assumes independent observations, but daily returns
   from a rule-based strategy can be serially correlated (e.g. a position
   held across several days moves with the same trend for all of them).
   A Ljung-Box test flags when that assumption looks shaky for a given
   model's return series, so the p-value/effect-size can be read with the
   appropriate grain of salt rather than taken at face value.

Follow-up (2026-09-14, same day as Task #23): the autocorrelation warning
above is only a flag -- it never changes the reported p-value, even
though Welch's t-test can genuinely be anti-conservative (falsely
"significant" more often than the nominal 5%) when the underlying data
is serially correlated, because autocorrelated observations carry less
independent information than the same count of i.i.d. points would.
ComparisonResult.p_value_block_bootstrap (via block_bootstrap_pvalue)
adds a second, assumption-light p-value for the same null hypothesis
(equal means) computed with a moving block bootstrap, which resamples
whole contiguous blocks of each series (rather than single points) and
so preserves each series' own serial-correlation structure in the
resampling distribution. It is reported alongside the t-test's p-value
rather than replacing it, since it is a good cross-check regardless of
whether the Ljung-Box test happens to flag a given series.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.stats.diagnostic import acorr_ljungbox


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
    cohens_d: float | None = None
    p_value_adjusted: float | None = None
    autocorrelation_warning_a: bool | None = None
    autocorrelation_warning_b: bool | None = None
    p_value_block_bootstrap: float | None = None

    @property
    def significant_at_5pct(self) -> bool:
        return self.p_value is not None and self.p_value < 0.05

    @property
    def significant_after_correction(self) -> bool:
        """Significance using the multiple-comparison-adjusted p-value
        (set by adjust_for_multiple_comparisons / compare_all_pairs)
        rather than the raw pairwise p-value. False (not just "unknown")
        when no correction has been applied yet, since an uncorrected
        result should not be read as having passed correction."""
        return self.p_value_adjusted is not None and self.p_value_adjusted < 0.05


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float | None:
    """Effect size for two independent samples, using the pooled standard
    deviation: (mean_a - mean_b) / pooled_std. None when the pooled
    variance is exactly zero (e.g. both groups flat at 0.0) -- undefined,
    same treatment as t_statistic/p_value rather than a raw inf/NaN."""
    n_a, n_b = len(a), len(b)
    var_a, var_b = np.var(a, ddof=1), np.var(b, ddof=1)
    pooled_var = ((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2)
    if pooled_var <= 0:
        return None
    return float((np.mean(a) - np.mean(b)) / np.sqrt(pooled_var))


def _autocorrelation_warning(series: np.ndarray, min_obs: int = 10) -> bool | None:
    """Ljung-Box test for serial correlation in a daily-return series, at
    lag min(5, n//2). Returns True when significant autocorrelation is
    detected (p<0.05) -- a caveat on this series' t-test/effect-size
    results, not a reason to discard them. Returns None below `min_obs`
    observations rather than reporting a result from too little data to
    be meaningful (same graceful-degradation posture as the rest of sw2)."""
    n = len(series)
    if n < min_obs:
        return None
    lags = max(1, min(5, n // 2))
    lb_result = acorr_ljungbox(series, lags=[lags], return_df=True)
    return bool(lb_result["lb_pvalue"].iloc[0] < 0.05)


def _default_block_size(n: int) -> int:
    """Politis & White-style default block length for the moving block
    bootstrap: roughly the cube root of the sample size (a standard
    rule-of-thumb scaling for block length in serially-correlated data),
    clamped to [1, n // 2] so a single block never spans (or nearly
    spans) the whole series -- that would leave no room to actually
    resample anything."""
    if n <= 1:
        return 1
    size = int(round(n ** (1 / 3)))
    return max(1, min(size, max(1, n // 2)))


def _moving_block_bootstrap_mean(series: np.ndarray, block_size: int, rng: np.random.Generator) -> float:
    """Resample `series` via the moving block bootstrap: repeatedly draw a
    random starting index and take the `block_size` consecutive
    observations that follow it (blocks may overlap and be reused),
    concatenate blocks until reaching at least the original length, then
    trim to exactly that length. Unlike an ordinary (single-point) i.i.d.
    bootstrap, resampling whole contiguous blocks preserves the series'
    own serial-correlation structure in the resampled data. Returns the
    resampled series' mean."""
    n = len(series)
    block_size = min(block_size, n)
    n_blocks_needed = -(-n // block_size)  # ceil division
    starts = rng.integers(0, n - block_size + 1, size=n_blocks_needed)
    resampled = np.concatenate([series[s : s + block_size] for s in starts])[:n]
    return float(np.mean(resampled))


def block_bootstrap_pvalue(
    a: np.ndarray,
    b: np.ndarray,
    n_resamples: int = 2000,
    block_size: int | None = None,
    random_state: int | np.random.Generator | None = None,
) -> float | None:
    """Two-sided p-value for a difference in means between two
    (possibly serially-correlated) series, via the moving block
    bootstrap -- see the module docstring's "Follow-up" note for why
    this exists alongside compare_daily_returns' Welch's t-test.

    Procedure: each series is first centered on its own mean (a - mean(a),
    b - mean(b)), which enforces the null hypothesis of equal means while
    preserving each series' own variance and autocorrelation structure.
    Blocks of `block_size` consecutive observations (default: a
    Politis-White-style n**(1/3) rule, see _default_block_size) are then
    resampled with replacement from each centered series independently,
    `n_resamples` times, to build a null distribution of the resampled
    mean difference. The reported p-value is the fraction of that null
    distribution at least as extreme (in absolute value) as the actually
    observed mean(a) - mean(b), with add-one smoothing (Davison & Hinkley)
    so a p-value of exactly 0 is never reported from a finite number of
    resamples.

    Returns None when either series has fewer than 4 observations -- too
    little data for a block of any meaningful length to say anything.
    random_state accepts an int seed or a numpy Generator; pass a fixed
    seed for reproducible results across repeated runs on the same data
    (the default None draws fresh randomness each call).
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    if len(a) < 4 or len(b) < 4:
        return None

    rng = random_state if isinstance(random_state, np.random.Generator) else np.random.default_rng(random_state)

    observed_diff = float(np.mean(a) - np.mean(b))
    a_centered = a - np.mean(a)
    b_centered = b - np.mean(b)
    block_a = block_size or _default_block_size(len(a))
    block_b = block_size or _default_block_size(len(b))

    at_least_as_extreme = 0
    for _ in range(n_resamples):
        boot_diff = _moving_block_bootstrap_mean(a_centered, block_a, rng) - _moving_block_bootstrap_mean(
            b_centered, block_b, rng
        )
        if abs(boot_diff) >= abs(observed_diff):
            at_least_as_extreme += 1
    return (at_least_as_extreme + 1) / (n_resamples + 1)


def compare_daily_returns(
    model_a_name: str,
    returns_a: pd.Series,
    model_b_name: str,
    returns_b: pd.Series,
    *,
    bootstrap_resamples: int = 2000,
    bootstrap_random_state: int | None = 0,
) -> ComparisonResult:
    """Welch's t-test (unequal variance) on two models' daily-return
    series -- doesn't assume equal variance since different rule sets
    can easily end up with very different volatility. Also reports
    Cohen's d (effect size) and, per series, whether a Ljung-Box test
    flags significant autocorrelation (a caveat on the i.i.d. assumption
    both the t-test and Cohen's d rely on) -- see the module docstring
    for why (Task #23). p_value_adjusted is left None here; it is filled
    in afterward by adjust_for_multiple_comparisons / compare_all_pairs,
    since a multiple-comparison correction is only meaningful across a
    batch of comparisons, not for one pair in isolation.

    Early in a model's life (or for any model that simply hasn't traded
    yet) its daily returns are all exactly 0.0 -- zero variance. Welch's
    t-statistic is then a 0/0 division and scipy returns nan/nan. That is
    a genuinely undefined test outcome, not "no difference detected", so
    it's reported as t_statistic=None/p_value=None rather than a raw NaN
    (cohens_d gets the same treatment for the same reason). This also
    matters for the JSON this feeds (data/sw2/comparisons/latest.json):
    Python's json module happily writes a bare `NaN` token, which is
    invalid JSON and breaks the dashboard's JSON.parse -- None serializes
    as `null`, which every JSON parser accepts.

    bootstrap_resamples/bootstrap_random_state control the block-bootstrap
    p-value (see block_bootstrap_pvalue): bootstrap_random_state defaults
    to a fixed seed (0) rather than None so that re-running the daily
    pipeline against the same underlying returns (e.g. a manual workflow
    re-run for verification) reproduces the same p_value_block_bootstrap
    rather than a slightly different one each time from fresh randomness.
    """
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
        cohens_d=_cohens_d(a, b),
        autocorrelation_warning_a=_autocorrelation_warning(a),
        autocorrelation_warning_b=_autocorrelation_warning(b),
        p_value_block_bootstrap=block_bootstrap_pvalue(
            a, b, n_resamples=bootstrap_resamples, random_state=bootstrap_random_state
        ),
    )


def adjust_for_multiple_comparisons(
    results: list[ComparisonResult],
    method: str = "benjamini-hochberg",
) -> list[ComparisonResult]:
    """Apply a multiple-comparison correction across a batch of pairwise
    ComparisonResults, returning new ComparisonResult objects with
    p_value_adjusted filled in (originals are left untouched).

    compare_all_pairs runs one independent hypothesis test per model
    pair -- with N models that's N*(N-1)/2 tests, and reading each one's
    raw p_value < 0.05 as "significant" on its own inflates the batch's
    overall false-positive rate as N grows (the classic multiple-
    comparisons problem). This corrects for that across whichever set of
    pairs is passed in.

    method="benjamini-hochberg" (default): controls the false discovery
    rate (FDR) -- the expected fraction of "significant" pairs that are
    actually false positives. Less conservative than Bonferroni, and the
    usual choice when reporting a table of many pairwise comparisons
    together (as opposed to one preregistered test).
    method="bonferroni": controls the family-wise error rate (the
    probability of ANY false positive across the batch) -- more
    conservative, appropriate when even a single false-positive pairwise
    claim would be costly.

    Results with p_value=None (undefined test, e.g. zero variance in
    both groups) get p_value_adjusted=None and are excluded from the
    correction's denominator -- they were never candidates for
    significance in the first place, so including them would only make
    the correction unnecessarily conservative for the rest of the batch.

    This only adjusts the Welch's t-test p_value -> p_value_adjusted;
    p_value_block_bootstrap is left as-is. It is reported as a per-pair
    robustness cross-check on the t-test result rather than a second
    "primary" test, so folding it into the same multiple-comparisons
    correction as p_value would conflate two different p-values under
    one adjustment and make the corrected numbers harder to reason about.
    """
    p_values = [r.p_value for r in results]
    indices_with_p = [i for i, p in enumerate(p_values) if p is not None]
    if not indices_with_p:
        return [dataclasses.replace(r, p_value_adjusted=None) for r in results]

    raw = np.array([p_values[i] for i in indices_with_p], dtype=float)
    if method == "bonferroni":
        m = len(raw)
        adjusted = np.minimum(raw * m, 1.0)
    elif method == "benjamini-hochberg":
        adjusted = stats.false_discovery_control(raw, method="bh")
    else:
        raise ValueError(f"unknown method: {method!r} (expected 'benjamini-hochberg' or 'bonferroni')")

    adjusted_by_index = {idx: float(val) for idx, val in zip(indices_with_p, adjusted)}
    return [dataclasses.replace(r, p_value_adjusted=adjusted_by_index.get(i)) for i, r in enumerate(results)]


def compare_all_pairs(
    returns_by_model: dict[str, pd.Series],
    correction_method: str | None = "benjamini-hochberg",
) -> list[ComparisonResult]:
    """Pairwise comparison across every registered model with enough
    history -- this is what a leaderboard / summary report reads from.
    Pairs without enough history yet are silently skipped rather than
    raising, since early in the project most pairs won't have 2+ days.

    By default the whole batch of pairs is run through
    adjust_for_multiple_comparisons (Benjamini-Hochberg FDR) before being
    returned, since these results are always reported together as a
    table -- pass correction_method=None to get the raw, uncorrected
    per-pair p-values only (e.g. for testing a single pair in isolation)."""
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
    if correction_method is not None and results:
        results = adjust_for_multiple_comparisons(results, method=correction_method)
    return results

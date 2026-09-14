"""
SW2 -- governance criteria for promoting/holding a model (Task #25).

Before this module there was no code defining what "this model is doing
better" should actually mean for automated decision-making -- only a
statistical comparison (sw2.compare) that a human would have to read and
judge. This combines three checks into one rule, deliberately conservative
(any one check failing holds the verdict at "hold" or "insufficient_data"
rather than promoting on a partial signal):

1. Minimum sample size (n >= GOVERNANCE_MIN_N trading days for BOTH models)
   -- a t-test/effect-size on a handful of days is not something to act on,
   however small the p-value happens to look.
2. Statistical significance at `alpha`, preferring the Task #23 multiple-
   comparison-corrected p-value (ComparisonResult.p_value_adjusted) over the
   raw per-pair p-value when it's available -- this module exists to gate an
   automated decision, which is exactly the situation the correction is for.
3. A minimum current streak of consecutive days where model_a's daily
   return beat model_b's (GOVERNANCE_MIN_CONSECUTIVE_ADVANTAGE_DAYS) --
   guards against promoting on the strength of a lead that was built entirely
   in the past and has since reversed, which a batch-level mean/t-test alone
   wouldn't catch. Only checked when the caller supplies the two models'
   daily-return series; skipped (not treated as a failure) when they aren't,
   since a ComparisonResult alone doesn't carry day-by-day history.

This governs promotion of model_a specifically (the comparison's first
model) over model_b -- evaluate both orders if you need a symmetric
leaderboard-style verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from sw2.compare import ComparisonResult

GOVERNANCE_MIN_N = 20  # minimum trading days of history per model
GOVERNANCE_ALPHA = 0.05  # significance threshold
GOVERNANCE_MIN_CONSECUTIVE_ADVANTAGE_DAYS = 5  # current win-streak required, when daily returns are supplied


@dataclass
class GovernanceVerdict:
    model_a: str
    model_b: str
    verdict: str  # "promote" | "hold" | "insufficient_data"
    reasons: list[str] = field(default_factory=list)
    consecutive_advantage_days_a: int | None = None

    @property
    def promotable(self) -> bool:
        return self.verdict == "promote"


def consecutive_advantage_days(returns_a: pd.Series, returns_b: pd.Series) -> int:
    """Length of model_a's current win-streak over model_b: counting
    backward from the most recent trading day, how many days in a row has
    a's daily return strictly beaten b's? Stops at the first day that
    isn't a win for a (or a missing/NaN value on either side), so a
    single bad day resets the streak to 0 even if a was winning before
    that. The two series are compared positionally (last row vs last row,
    and so on) -- callers should pass same-length, same-date-aligned
    daily-return series, e.g. as produced by _finalize_model_run."""
    a = returns_a.reset_index(drop=True)
    b = returns_b.reset_index(drop=True)
    n = min(len(a), len(b))
    streak = 0
    for i in range(n - 1, -1, -1):
        a_i, b_i = a.iloc[i], b.iloc[i]
        if pd.isna(a_i) or pd.isna(b_i):
            break
        if a_i > b_i:
            streak += 1
        else:
            break
    return streak


def evaluate_promotion(
    comparison: ComparisonResult,
    returns_a: pd.Series | None = None,
    returns_b: pd.Series | None = None,
    *,
    min_n: int = GOVERNANCE_MIN_N,
    alpha: float = GOVERNANCE_ALPHA,
    min_consecutive_advantage_days: int = GOVERNANCE_MIN_CONSECUTIVE_ADVANTAGE_DAYS,
) -> GovernanceVerdict:
    """Decide whether `comparison.model_a` has earned promotion over
    `comparison.model_b`, per the module docstring's three checks. Returns
    a GovernanceVerdict with a verdict string and the reasons that led to
    it -- reasons accumulate caveats (uncorrected p-value, autocorrelation
    warning) even on a "promote" verdict, so the record shows exactly how
    confident this call actually is, not just a bare yes/no.

    Deliberately conservative in the failure modes it can't tell apart
    from success: an undefined t-test (zero variance) and a p-value that
    simply isn't significant both come back as "hold", never "promote".
    Too little history comes back as "insufficient_data" (distinct from
    "hold") so a caller can tell "not enough evidence yet" apart from
    "evidence says no"."""
    reasons: list[str] = []

    if comparison.n_a < min_n or comparison.n_b < min_n:
        reasons.append(f"sample size too small: n_a={comparison.n_a}, n_b={comparison.n_b} (need >= {min_n} each)")
        return GovernanceVerdict(comparison.model_a, comparison.model_b, "insufficient_data", reasons)

    used_uncorrected = comparison.p_value_adjusted is None
    p_used = comparison.p_value_adjusted if not used_uncorrected else comparison.p_value
    if used_uncorrected and comparison.p_value is not None:
        reasons.append(
            "no multiple-comparison-corrected p-value available -- using the raw (uncorrected) p-value; "
            "treat this verdict as provisional (run this comparison through "
            "sw2.compare.adjust_for_multiple_comparisons first if it's part of a larger batch)"
        )

    if comparison.autocorrelation_warning_a or comparison.autocorrelation_warning_b:
        reasons.append(
            "significant autocorrelation detected in at least one model's daily returns (Ljung-Box) -- "
            "the t-test's independence assumption may not hold for this comparison"
        )

    if p_used is None:
        reasons.append("t-test undefined (zero variance in both groups) -- cannot assess significance")
        return GovernanceVerdict(comparison.model_a, comparison.model_b, "hold", reasons)

    if p_used >= alpha:
        reasons.append(f"not statistically significant: p={p_used:.4f} >= alpha={alpha}")
        return GovernanceVerdict(comparison.model_a, comparison.model_b, "hold", reasons)

    if comparison.mean_daily_return_a <= comparison.mean_daily_return_b:
        reasons.append(
            f"{comparison.model_a}'s mean daily return ({comparison.mean_daily_return_a:.5f}) does not exceed "
            f"{comparison.model_b}'s ({comparison.mean_daily_return_b:.5f}) -- significance alone doesn't "
            f"justify promoting {comparison.model_a}"
        )
        return GovernanceVerdict(comparison.model_a, comparison.model_b, "hold", reasons)

    streak: int | None = None
    if returns_a is not None and returns_b is not None:
        streak = consecutive_advantage_days(returns_a, returns_b)
        if streak < min_consecutive_advantage_days:
            reasons.append(f"current advantage streak too short: {streak} day(s) < required {min_consecutive_advantage_days}")
            return GovernanceVerdict(
                comparison.model_a, comparison.model_b, "hold", reasons, consecutive_advantage_days_a=streak
            )
        reasons.append(f"current advantage streak: {streak} day(s) >= required {min_consecutive_advantage_days}")

    reasons.append(
        f"n>={min_n} for both models, p={p_used:.4f} < alpha={alpha}, "
        f"{comparison.model_a} leads {comparison.model_b} on mean daily return"
    )
    return GovernanceVerdict(comparison.model_a, comparison.model_b, "promote", reasons, consecutive_advantage_days_a=streak)

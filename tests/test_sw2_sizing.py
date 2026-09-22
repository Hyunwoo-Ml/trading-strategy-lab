import pytest

from sw2.sizing import (
    MAX_TRANCHE_FRACTION,
    MIN_TRANCHE_FRACTION,
    RISK_FRACTION,
    risk_based_tranche_dollars,
)


def test_typical_price_criteria_stop_loss_sizes_to_ten_percent():
    # SW1's default price-criteria stop_loss_pct is -0.08; after the
    # 2026-09-22 benchmark-gap retune, RISK_FRACTION=0.008 sizes this at
    # 10% of starting cash (0.008 / 0.08 = 0.10) -- double the pre-retune
    # ~5% tranche, see sw2/sizing.py's module docstring for why.
    dollars = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.08)
    assert dollars == pytest.approx(10_000.0)


def test_wider_stop_gives_smaller_tranche_than_tighter_stop():
    tight = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.05)
    wide = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.10)
    assert wide < tight


def test_unclamped_fraction_matches_risk_over_stop_distance():
    # stop_distance=0.10 -> fraction = 0.008 / 0.10 = 0.08, inside
    # [0.03, 0.20] so it should NOT be clamped.
    dollars = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.10)
    assert dollars == pytest.approx(100_000.0 * (RISK_FRACTION / 0.10))
    assert dollars == pytest.approx(8_000.0)


def test_extremely_tight_stop_clamped_to_max_fraction():
    dollars = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.01)
    assert dollars == pytest.approx(100_000.0 * MAX_TRANCHE_FRACTION)


def test_extremely_wide_stop_clamped_to_min_fraction():
    dollars = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.50)
    assert dollars == pytest.approx(100_000.0 * MIN_TRANCHE_FRACTION)


def test_zero_stop_loss_pct_falls_back_to_midpoint():
    dollars = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=0.0)
    midpoint = (MIN_TRANCHE_FRACTION + MAX_TRANCHE_FRACTION) / 2
    assert dollars == pytest.approx(100_000.0 * midpoint)


def test_positive_stop_loss_pct_treated_via_absolute_value():
    negative = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.15)
    positive = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=0.15)
    assert positive == pytest.approx(negative)


def test_custom_risk_fraction_scales_tranche_size():
    # Both explicit risk_fractions chosen to land inside [MIN_TRANCHE_FRACTION,
    # MAX_TRANCHE_FRACTION] = [0.03, 0.20] at stop_distance=0.10 (0.004/0.10=0.04,
    # 0.008/0.10=0.08) so the 2x relationship isn't obscured by clamping.
    small_risk = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.10, risk_fraction=0.004)
    default_risk = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.10, risk_fraction=0.008)
    assert small_risk == pytest.approx(default_risk / 2)


def test_custom_bounds_are_respected():
    dollars = risk_based_tranche_dollars(
        starting_cash=100_000.0,
        stop_loss_pct=-0.01,
        min_fraction=0.02,
        max_fraction=0.06,
    )
    assert dollars == pytest.approx(100_000.0 * 0.06)


def test_scales_linearly_with_starting_cash():
    small = risk_based_tranche_dollars(starting_cash=50_000.0, stop_loss_pct=-0.08)
    large = risk_based_tranche_dollars(starting_cash=100_000.0, stop_loss_pct=-0.08)
    assert large == pytest.approx(small * 2)

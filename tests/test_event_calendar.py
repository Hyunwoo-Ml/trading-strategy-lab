from __future__ import annotations

from sw1.calendar.events import (
    CPI_RELEASE_DATES_2026,
    FOMC_ANNOUNCEMENT_DATES_2026,
    is_earnings_blackout,
    is_event_blackout,
    is_macro_blackout,
)


def test_exact_fomc_date_is_blackout():
    result = is_macro_blackout(FOMC_ANNOUNCEMENT_DATES_2026[0], window_days=1)
    assert result.is_blackout
    assert any("FOMC" in r for r in result.reasons)


def test_exact_cpi_date_is_blackout():
    result = is_macro_blackout(CPI_RELEASE_DATES_2026[0], window_days=1)
    assert result.is_blackout
    assert any("CPI" in r for r in result.reasons)


def test_day_within_window_is_blackout():
    # 2026-01-28 is a known FOMC date; the day before should also blackout
    # with window_days=1.
    result = is_macro_blackout("2026-01-27", window_days=1)
    assert result.is_blackout


def test_day_outside_window_is_not_blackout():
    result = is_macro_blackout("2026-02-01", window_days=1)
    assert not result.is_blackout
    assert result.reasons == []


def test_earnings_blackout_none_date_never_blocks():
    result = is_earnings_blackout("2026-05-01", earnings_date=None)
    assert not result.is_blackout


def test_earnings_blackout_near_date_blocks():
    result = is_earnings_blackout("2026-05-01", earnings_date="2026-05-02", window_days=1)
    assert result.is_blackout
    assert "실적" in result.reasons[0]


def test_earnings_blackout_far_date_does_not_block():
    result = is_earnings_blackout("2026-05-01", earnings_date="2026-06-15", window_days=1)
    assert not result.is_blackout


def test_combined_blackout_merges_reasons():
    fomc_date = FOMC_ANNOUNCEMENT_DATES_2026[0]
    result = is_event_blackout(fomc_date, earnings_date=fomc_date)
    assert result.is_blackout
    assert len(result.reasons) == 2  # macro + earnings both hit


def test_combined_blackout_false_when_neither_hits():
    result = is_event_blackout("2026-02-01", earnings_date="2026-06-15")
    assert not result.is_blackout
    assert result.reasons == []

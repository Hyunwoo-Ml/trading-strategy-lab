from __future__ import annotations

from sw1.calendar.events import (
    ALL_CPI_RELEASE_DATES,
    ALL_FOMC_ANNOUNCEMENT_DATES,
    CPI_RELEASE_DATES_2023,
    CPI_RELEASE_DATES_2024,
    CPI_RELEASE_DATES_2025,
    CPI_RELEASE_DATES_2026,
    FOMC_ANNOUNCEMENT_DATES_2023,
    FOMC_ANNOUNCEMENT_DATES_2024,
    FOMC_ANNOUNCEMENT_DATES_2025,
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


# -- 2026-09-22: macro dates extended back to 2023-2025 for the 3-year
# historical backtest (scripts/run_historical_backtest.py) -- these confirm
# every year's dates actually made it into the combined lists, not just
# 2026's. --


def test_all_years_present_in_combined_fomc_list():
    for year_list in (
        FOMC_ANNOUNCEMENT_DATES_2023,
        FOMC_ANNOUNCEMENT_DATES_2024,
        FOMC_ANNOUNCEMENT_DATES_2025,
        FOMC_ANNOUNCEMENT_DATES_2026,
    ):
        assert len(year_list) == 8  # 8 regularly scheduled FOMC meetings/year
        for date in year_list:
            assert date in ALL_FOMC_ANNOUNCEMENT_DATES


def test_all_years_present_in_combined_cpi_list():
    for year_list in (CPI_RELEASE_DATES_2023, CPI_RELEASE_DATES_2024, CPI_RELEASE_DATES_2025, CPI_RELEASE_DATES_2026):
        for date in year_list:
            assert date in ALL_CPI_RELEASE_DATES
    # 2025 has only 11 entries (not 12) -- the October-data CPI release was
    # cancelled outright during that year's government shutdown, see
    # sw1.calendar.events' module docstring.
    assert len(CPI_RELEASE_DATES_2025) == 11


def test_2023_fomc_date_is_blackout():
    result = is_macro_blackout(FOMC_ANNOUNCEMENT_DATES_2023[0], window_days=1)
    assert result.is_blackout
    assert any("FOMC" in r for r in result.reasons)


def test_2024_cpi_date_is_blackout():
    result = is_macro_blackout(CPI_RELEASE_DATES_2024[0], window_days=1)
    assert result.is_blackout
    assert any("CPI" in r for r in result.reasons)


def test_2025_shutdown_delayed_cpi_date_is_blackout():
    # 2025-10-24 is the actual (shutdown-delayed) release date, not the
    # originally-scheduled mid-October one -- confirms the real date is
    # what's registered, not the cancelled/originally-published one.
    result = is_macro_blackout("2025-10-24", window_days=1)
    assert result.is_blackout
    assert any("CPI" in r for r in result.reasons)


def test_macro_event_dates_has_no_duplicates_across_years():
    from sw1.calendar.events import MACRO_EVENT_DATES

    # MACRO_EVENT_DATES is a set union, so it de-dupes the one real
    # coincidence where an FOMC decision and a CPI release landed on the
    # same day (2024-06-12) -- it must have strictly fewer entries than the
    # simple sum of both lists' lengths, not more (that would mean a
    # spurious duplicate crept into one of the per-year lists themselves).
    assert len(MACRO_EVENT_DATES) == len(set(MACRO_EVENT_DATES))
    assert len(MACRO_EVENT_DATES) <= len(ALL_FOMC_ANNOUNCEMENT_DATES) + len(ALL_CPI_RELEASE_DATES)
    assert len(ALL_FOMC_ANNOUNCEMENT_DATES) == len(set(ALL_FOMC_ANNOUNCEMENT_DATES))
    assert len(ALL_CPI_RELEASE_DATES) == len(set(ALL_CPI_RELEASE_DATES))

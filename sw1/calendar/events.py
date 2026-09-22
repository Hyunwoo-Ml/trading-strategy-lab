"""
SW1 -- macro/earnings event calendar.

Purpose: give sw2.price_criteria_model a reason to skip a *new* entry
(buy_1/buy_2) right around a scheduled high-volatility event (FOMC rate
decision, CPI release, or a ticker's own earnings), even if price has
technically touched a support level -- a touch driven by pre-event
positioning behaves very differently from an ordinary pullback. Per
2026-09-13 feedback ("FOMC, 실적발표일, 소비자지수발표 등등 많은 이벤트를
캘린더에 반영하는 로직도 추가").

STATUS / LIMITATIONS:
- FOMC and CPI dates below are hand-entered from the Federal Reserve's and
  BLS's published schedules (federalreserve.gov/monetarypolicy/
  fomccalendars.htm and the BLS CPI release calendar). There is no live
  calendar API wired in -- future years must be added by hand here.
- 2026-09-22: extended back to 2023-2025 (previously only 2026 was
  registered, so scripts/run_historical_backtest.py's ~3-year replay had no
  macro blackout filter at all before that year -- see this module's
  docstring note in that script). 2023/2024 FOMC+CPI dates are the actual
  historical release dates (federalreserve.gov meeting calendar archive,
  bls.gov/schedule/{year}/home.htm). 2025 CPI dates reflect what actually
  happened during that year's government shutdown, not the originally
  published schedule: the October-data release (normally mid-November) was
  cancelled outright, and the September-data release that would normally
  land in mid-October slipped to 2025-10-24; there is no separate
  "November 2025" entry distinct from the already-listed 2025-12-18 release
  (which covered November data, delayed from its usual early-December
  slot) -- using the dates that actually moved markets, not the
  originally-scheduled-then-cancelled ones, is what a backtest wants here.
- Per-ticker earnings dates are fetched from yfinance, best-effort and
  forward-looking only. yfinance does not reliably expose historical
  earnings dates far enough back for a multi-year backtest, so the
  earnings-blackout check is skipped (never raises) whenever no date is
  supplied -- callers running a backtest over years without a resolvable
  earnings date simply don't get that particular filter for those years.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

# Federal Reserve FOMC meeting dates (rate-decision/announcement day -- the
# second day of each 2-day meeting), by year.
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# (and the Fed's meeting-calendar archive for 2023/2024/2025).
FOMC_ANNOUNCEMENT_DATES_2023: list[str] = [
    "2023-02-01",
    "2023-03-22",
    "2023-05-03",
    "2023-06-14",
    "2023-07-26",
    "2023-09-20",
    "2023-11-01",
    "2023-12-13",
]

FOMC_ANNOUNCEMENT_DATES_2024: list[str] = [
    "2024-01-31",
    "2024-03-20",
    "2024-05-01",
    "2024-06-12",
    "2024-07-31",
    "2024-09-18",
    "2024-11-07",
    "2024-12-18",
]

FOMC_ANNOUNCEMENT_DATES_2025: list[str] = [
    "2025-01-29",
    "2025-03-19",
    "2025-05-07",
    "2025-06-18",
    "2025-07-30",
    "2025-09-17",
    "2025-10-29",
    "2025-12-10",
]

FOMC_ANNOUNCEMENT_DATES_2026: list[str] = [
    "2026-01-28",
    "2026-03-18",
    "2026-04-29",
    "2026-06-17",
    "2026-07-29",
    "2026-09-16",
    "2026-10-28",
    "2026-12-09",
]

# US CPI release dates by year. Source: bls.gov/schedule/{year}/home.htm
# (the actual release date, which for 2025 sometimes differs from the
# originally-published schedule -- see the shutdown note above).
CPI_RELEASE_DATES_2023: list[str] = [
    "2023-01-12",
    "2023-02-14",
    "2023-03-14",
    "2023-04-12",
    "2023-05-10",
    "2023-06-13",
    "2023-07-12",
    "2023-08-10",
    "2023-09-13",
    "2023-10-12",
    "2023-11-14",
    "2023-12-12",
]

CPI_RELEASE_DATES_2024: list[str] = [
    "2024-01-11",
    "2024-02-13",
    "2024-03-12",
    "2024-04-10",
    "2024-05-15",
    "2024-06-12",
    "2024-07-11",
    "2024-08-14",
    "2024-09-11",
    "2024-10-10",
    "2024-11-13",
    "2024-12-11",
]

# 2025-10-24 (not the originally-scheduled mid-October date) and
# 2025-12-18 (November data, delayed) are the actual shutdown-affected
# release dates -- see the shutdown note in this module's docstring. There
# is no separate October-data release; it was cancelled outright.
CPI_RELEASE_DATES_2025: list[str] = [
    "2025-01-15",
    "2025-02-12",
    "2025-03-12",
    "2025-04-10",
    "2025-05-13",
    "2025-06-11",
    "2025-07-15",
    "2025-08-12",
    "2025-09-11",
    "2025-10-24",
    "2025-12-18",
]

CPI_RELEASE_DATES_2026: list[str] = [
    "2026-01-13",
    "2026-02-13",
    "2026-03-11",
    "2026-04-10",
    "2026-05-12",
    "2026-06-10",
    "2026-07-14",
    "2026-08-12",
    "2026-09-11",
    "2026-10-14",
    "2026-11-10",
    "2026-12-10",
]

ALL_FOMC_ANNOUNCEMENT_DATES: list[str] = (
    FOMC_ANNOUNCEMENT_DATES_2023
    + FOMC_ANNOUNCEMENT_DATES_2024
    + FOMC_ANNOUNCEMENT_DATES_2025
    + FOMC_ANNOUNCEMENT_DATES_2026
)
ALL_CPI_RELEASE_DATES: list[str] = (
    CPI_RELEASE_DATES_2023 + CPI_RELEASE_DATES_2024 + CPI_RELEASE_DATES_2025 + CPI_RELEASE_DATES_2026
)

MACRO_EVENT_DATES: list[str] = sorted(set(ALL_FOMC_ANNOUNCEMENT_DATES) | set(ALL_CPI_RELEASE_DATES))


@dataclass
class EventBlackoutResult:
    is_blackout: bool
    reasons: list[str] = field(default_factory=list)


def _parse(date_str: str) -> dt.date:
    return dt.date.fromisoformat(str(date_str)[:10])


def is_macro_blackout(date: str, window_days: int = 1) -> EventBlackoutResult:
    """True if `date` falls within `window_days` (inclusive, calendar days)
    of any known FOMC announcement or CPI release date."""
    target = _parse(date)
    reasons = []
    for event_date_str in MACRO_EVENT_DATES:
        event_date = _parse(event_date_str)
        if abs((target - event_date).days) <= window_days:
            label = "FOMC" if event_date_str in ALL_FOMC_ANNOUNCEMENT_DATES else "CPI"
            reasons.append(f"{label} 발표일({event_date_str}) 전후 {window_days}일 이내")
    return EventBlackoutResult(is_blackout=bool(reasons), reasons=reasons)


def is_earnings_blackout(date: str, earnings_date: str | None, window_days: int = 1) -> EventBlackoutResult:
    """earnings_date is optional -- when the caller couldn't resolve a
    ticker's earnings date, this always returns not-blackout rather than
    raising, so the earnings check degrades gracefully instead of blocking
    every trade."""
    if not earnings_date:
        return EventBlackoutResult(is_blackout=False, reasons=[])
    target = _parse(date)
    event_date = _parse(earnings_date)
    if abs((target - event_date).days) <= window_days:
        return EventBlackoutResult(
            is_blackout=True,
            reasons=[f"실적 발표일({earnings_date}) 전후 {window_days}일 이내"],
        )
    return EventBlackoutResult(is_blackout=False, reasons=[])


def is_event_blackout(
    date: str,
    earnings_date: str | None = None,
    macro_window_days: int = 1,
    earnings_window_days: int = 1,
) -> EventBlackoutResult:
    """Combined macro (FOMC/CPI) + ticker earnings blackout check."""
    macro = is_macro_blackout(date, window_days=macro_window_days)
    earnings = is_earnings_blackout(date, earnings_date, window_days=earnings_window_days)
    return EventBlackoutResult(
        is_blackout=macro.is_blackout or earnings.is_blackout,
        reasons=macro.reasons + earnings.reasons,
    )


def fetch_next_earnings_date(ticker: str) -> str | None:
    """Best-effort forward-looking earnings date via yfinance. Only usable
    where yfinance has network access (GitHub Actions), never in the
    Cowork sandbox -- same constraint as sw1.data.yahoo. Returns None on
    any failure (network, missing data, unexpected shape) so callers
    degrade gracefully instead of crashing the daily pipeline over a
    calendar lookup."""
    try:
        import pandas as pd
        import yfinance as yf

        earnings = yf.Ticker(ticker).get_earnings_dates(limit=8)
        if earnings is None or len(earnings) == 0:
            return None
        now = pd.Timestamp.now(tz=earnings.index.tz) if earnings.index.tz else pd.Timestamp.now()
        nearest = min(earnings.index, key=lambda ts: abs((ts - now).total_seconds()))
        return nearest.date().isoformat()
    except Exception:
        return None

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
  BLS's published 2026 schedules (federalreserve.gov/monetarypolicy/
  fomccalendars.htm and the BLS CPI release calendar). There is no live
  calendar API wired in -- future years must be added by hand here.
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

# Federal Reserve 2026 FOMC meeting dates (rate-decision/announcement day --
# the second day of each 2-day meeting).
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
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

# US CPI release dates 2026. Source: BLS CPI release schedule.
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

MACRO_EVENT_DATES: list[str] = sorted(set(FOMC_ANNOUNCEMENT_DATES_2026) | set(CPI_RELEASE_DATES_2026))


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
            label = "FOMC" if event_date_str in FOMC_ANNOUNCEMENT_DATES_2026 else "CPI"
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

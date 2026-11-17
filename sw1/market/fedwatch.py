"""
SW1 -- next-FOMC rate odds from 30-day fed funds futures, the way CME's
FedWatch reads them (a free approximation, not CME's published numbers).

A fed funds futures contract settles at 100 minus the month's average
effective fed funds rate. For the month of the next meeting:

    avg(M) = (d / N) * r_start + ((N - d) / N) * r_end

where d is the number of days in the month at the old rate (through the
decision day), N the days in the month. r_start comes from the previous
month's contract when that month had no meeting (its average is the rate
going into the meeting); otherwise from the effective rate. Solving for r_end
and dividing the change by 25bp gives the probability of a move.
"""
from __future__ import annotations

import calendar
import math
from dataclasses import dataclass
from datetime import date

# Decision days (second day of each meeting), from federalreserve.gov.
# 2027 dates are tentative until confirmed at the preceding meeting.
FOMC_DECISIONS = (
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
    date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28), date(2027, 6, 9),
    date(2027, 7, 28), date(2027, 9, 15), date(2027, 10, 27), date(2027, 11, 17),
)

MONTH_CODES = "FGHJKMNQUVXZ"  # CME futures month letters, Jan..Dec
STEP = 0.25  # the Fed moves in 25bp steps


@dataclass
class FedOdds:
    meeting: str  # decision day, ISO
    current_range: str | None  # e.g. "3.75–4.00%"
    implied_change_bp: float  # expected change at the meeting, in bp
    hold: float  # probabilities, 0..1, summing to 1
    cut: float
    hike: float
    move_bp: int  # size of the most likely non-hold move, in bp (e.g. 25)
    outcomes: list  # [{"change_bp": -25, "prob": 0.7}, {"change_bp": 0, "prob": 0.3}], most likely first
    futures: dict  # contract symbol -> price, for transparency


def next_meeting(today: date) -> date | None:
    """The first decision day on or after `today`."""
    return next((d for d in FOMC_DECISIONS if d >= today), None)


def contract_code(year: int, month: int) -> str:
    """Root-relative CME code, e.g. Dec 2026 -> 'Z26'."""
    return f"{MONTH_CODES[month - 1]}{year % 100:02d}"


def prev_month(year: int, month: int) -> tuple[int, int]:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def _has_meeting(year: int, month: int) -> bool:
    return any(d.year == year and d.month == month for d in FOMC_DECISIONS)


def next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def odds_from_prices(meeting: date, price_meeting_month: float | None, price_prev_month: float | None,
                     effective_rate: float | None = None, target_upper: float | None = None,
                     price_next_month: float | None = None) -> FedOdds:
    """Probabilities for one meeting.

    Start rate: the previous month's contract if that month had no meeting,
    else `effective_rate`. End rate: the next month's contract if that month
    has no meeting (more reliable for late-month meetings, whose own month
    barely sees the new rate); otherwise backed out of the meeting month."""
    n = calendar.monthrange(meeting.year, meeting.month)[1]
    d = meeting.day  # days at the old rate: the new rate takes effect the day after the decision
    py, pm = prev_month(meeting.year, meeting.month)
    ny, nm = next_month(meeting.year, meeting.month)
    if price_prev_month is not None and not _has_meeting(py, pm):
        r_start = 100.0 - price_prev_month
    elif effective_rate is not None:
        r_start = effective_rate
    else:
        raise ValueError("need the previous month's contract or the effective rate")
    if price_next_month is not None and not _has_meeting(ny, nm):
        r_end = 100.0 - price_next_month
    else:
        if price_meeting_month is None:
            raise ValueError("need the meeting month's contract")
        if d >= n:
            raise ValueError("a last-day meeting can't be read from its own month's contract")
        avg = 100.0 - price_meeting_month
        r_end = (avg - (d / n) * r_start) / ((n - d) / n)
    change = r_end - r_start
    # The market prices a mix of the two 25bp outcomes either side of the
    # implied change: 1.4 steps of cut = 60% "25bp cut", 40% "50bp cut".
    steps = change / STEP
    lo = math.floor(steps)
    p_hi = steps - lo
    outcomes = {lo: 1.0 - p_hi, lo + 1: p_hi}
    hold = outcomes.get(0, 0.0)
    cut = sum(p for k, p in outcomes.items() if k < 0)
    hike = sum(p for k, p in outcomes.items() if k > 0)
    ranked = sorted(((k, p) for k, p in outcomes.items() if p > 0.0005), key=lambda kp: -kp[1])
    moving = [k for k, _ in ranked if k != 0]
    move = 25 * abs(moving[0]) if moving else 25
    rng = None
    if target_upper is not None:
        rng = f"{target_upper - 0.25:.2f}–{target_upper:.2f}%"
    return FedOdds(
        meeting=meeting.isoformat(), current_range=rng,
        implied_change_bp=round(change * 100, 1),
        hold=round(hold, 3), cut=round(cut, 3), hike=round(hike, 3), move_bp=move,
        outcomes=[{"change_bp": int(k * 25), "prob": round(p, 3)} for k, p in ranked],
        futures={},
    )

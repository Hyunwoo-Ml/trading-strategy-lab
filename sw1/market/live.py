"""
SW1 -- the intraday part of the main page: market strip quotes and the
next-FOMC odds, rebuilt every 15 minutes during US trading hours by
.github/workflows/market-live.yml. Everything else on the site stays
end-of-day (scripts/build_market_dashboard.py).

Uses the same MarketSource as the daily build, plus one extra series
lookup per fed funds futures contract.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from sw1.market.dashboard import MarketSource, build_strip, normalize_history
from sw1.market.fedwatch import FOMC_DECISIONS, contract_code, next_month, odds_from_prices, prev_month

NEW_YORK = ZoneInfo("America/New_York")
DECISION_HOUR_ET = 14  # statements come out at 2pm New York time


def futures_symbol(year: int, month: int) -> str:
    """Yahoo's symbol for a 30-day fed funds future, e.g. ZQZ26.CBT."""
    return f"ZQ{contract_code(year, month)}.CBT"


def upcoming_meeting(now_utc: datetime) -> date | None:
    """Next decision day, moving past today's meeting once its 2pm ET statement is out."""
    ny = now_utc.astimezone(NEW_YORK)
    for d in FOMC_DECISIONS:
        if d > ny.date() or (d == ny.date() and ny.hour < DECISION_HOUR_ET):
            return d
    return None


def _last_close(source: MarketSource, ticker: str) -> float | None:
    try:
        s = normalize_history(source.history(ticker, "5d"))["Close"].dropna()
    except Exception:  # noqa: BLE001 -- a missing contract just leaves that input empty
        return None
    return float(s.iloc[-1]) if len(s) else None


def _last_fred(source: MarketSource, series_id: str) -> float | None:
    try:
        s = source.fred(series_id).dropna()
    except Exception:  # noqa: BLE001
        return None
    return float(s.iloc[-1]) if len(s) else None


def build_fedwatch(source: MarketSource, now_utc: datetime) -> dict:
    meeting = upcoming_meeting(now_utc)
    if meeting is None:
        return {"error": "no scheduled meeting in the calendar"}
    y, m = meeting.year, meeting.month
    syms = {
        "meeting": futures_symbol(y, m),
        "prev": futures_symbol(*prev_month(y, m)),
        "next": futures_symbol(*next_month(y, m)),
    }
    prices = {k: _last_close(source, s) for k, s in syms.items()}
    if prices["meeting"] is None and prices["next"] is None:
        return {"meeting": meeting.isoformat(), "error": f"no price for {syms['meeting']}"}
    effr = _last_fred(source, "EFFR")
    upper = _last_fred(source, "DFEDTARU")
    try:
        odds = odds_from_prices(meeting, prices["meeting"], prices["prev"], effective_rate=effr,
                                target_upper=upper, price_next_month=prices["next"])
    except ValueError as e:
        return {"meeting": meeting.isoformat(), "error": str(e)}
    out = asdict(odds)
    out["futures"] = {syms[k]: prices[k] for k in syms if prices[k] is not None}
    out["effective_rate"] = effr
    return out


def build_live(source: MarketSource, now_utc: datetime | None = None) -> dict:
    now_utc = now_utc or datetime.now(timezone.utc)
    return {
        "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strip": build_strip(source),
        "fedwatch": build_fedwatch(source, now_utc),
    }

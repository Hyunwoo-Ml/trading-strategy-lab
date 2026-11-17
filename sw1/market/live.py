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
from sw1.market.news import FEEDS, MACRO_NEWS_TICKERS, keep_for_macro, keep_for_ticker, merge, parse_rss, parse_yahoo_news

NEW_YORK = ZoneInfo("America/New_York")
# Big names whose Yahoo headlines make up the 기업 뉴스 tab.
COMPANY_NEWS_TICKERS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "TSM", "PLTR")
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


def build_headlines(source: MarketSource, now_utc: datetime | None = None, limit: int = 40, translator=None) -> dict:
    """Main-page headlines: 지정학·매크로 (official feeds + market-wide Yahoo
    lists) and 기업 (Yahoo lists of the biggest names). Each source that fails
    is skipped and listed under `errors`."""
    now_utc = now_utc or datetime.now(timezone.utc)
    macro, company, errors = [], [], []
    fetch_text = getattr(source, "fetch_text", None)
    for label, url, base in FEEDS:
        if fetch_text is None:
            break
        try:
            macro += parse_rss(fetch_text(url), label, base)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{label}: {str(exc)[:80]}")
    news = getattr(source, "news", None)
    for tickers, bucket, kind in ((MACRO_NEWS_TICKERS, macro, "macro"), (COMPANY_NEWS_TICKERS, company, "company")):
        for t in tickers:
            if news is None:
                break
            try:
                got = parse_yahoo_news(news(t), None if kind == "macro" else t, kind)
                bucket += keep_for_macro(got) if kind == "macro" else keep_for_ticker(got, t)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{t}: {str(exc)[:80]}")
    macro, company = merge(macro, limit, now_utc), merge(company, limit, now_utc)
    if translator is not None:
        translator.annotate(macro + company)
    return {
        "generated_at": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "macro": macro,
        "company": company,
        "errors": errors,
    }

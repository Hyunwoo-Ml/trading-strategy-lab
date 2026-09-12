"""
SW1 -- Yahoo Finance connector (yfinance).

Only runs where outbound network to query*.finance.yahoo.com is allowed --
NOT this Cowork cloud sandbox (blocked by org egress policy), but fine on
GitHub Actions runners. See README for the network architecture notes.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

M7_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]


@dataclass
class FundamentalSnapshot:
    ticker: str
    per: float | None
    pbr: float | None


def fetch_ohlcv(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetches daily OHLCV history shaped for sw1.indicators.technical
    (Open, High, Low, Close, Volume columns, date-ascending index)."""
    hist = yf.Ticker(ticker).history(period=period)
    if hist.empty:
        raise RuntimeError(f"{ticker}: no price history returned")
    return hist[["Open", "High", "Low", "Close", "Volume"]]


def fetch_fundamentals(ticker: str) -> FundamentalSnapshot:
    """PER (trailingPE) / PBR (priceToBook) -- these don't come from price
    history, so they're attached to the scoring pipeline separately from
    the technical indicators in sw1.indicators.technical."""
    info = yf.Ticker(ticker).info
    return FundamentalSnapshot(
        ticker=ticker,
        per=info.get("trailingPE"),
        pbr=info.get("priceToBook"),
    )


def fetch_m7_snapshot(tickers: list[str] | None = None, pause_seconds: float = 0.5) -> dict[str, dict]:
    """Fetches OHLCV + fundamentals for each ticker. Returns per-ticker
    results plus a `_failures` list so a partial run doesn't crash the
    whole pipeline -- one bad ticker shouldn't block the other six."""
    tickers = tickers or M7_TICKERS
    results: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    for ticker in tickers:
        try:
            ohlcv = fetch_ohlcv(ticker)
            fundamentals = fetch_fundamentals(ticker)
            results[ticker] = {"ohlcv": ohlcv, "fundamentals": fundamentals}
        except Exception as exc:  # noqa: BLE001 -- surfaced via _failures, not raised
            failures.append((ticker, str(exc)))
        time.sleep(pause_seconds)

    results["_failures"] = failures
    return results

"""
SW1 -- daily data collection driver.

Meant to run on a GitHub Actions runner (scheduled via
.github/workflows/collect-daily-data.yml), NOT in the Cowork cloud sandbox
(blocked by org egress policy -- see sw1/data/yahoo.py docstring).

For each M7 ticker this:
  1. Fetches OHLCV + fundamentals via sw1.data.yahoo.fetch_m7_snapshot
  2. Computes technical indicators via sw1.indicators.technical
  3. Computes a quant-only score via sw1.scoring.integrate.compute_quant_score
  4. Looks up that ticker's news_score (sw1/news/scorer.py), if any --
     2026-09-15: news input moved from a single weekly-overwrite .txt file
     per ticker to an append-only per-ticker log
     (sw1/news/input/{TICKER}.jsonl) plus one shared ticker-agnostic log
     (sw1/news/input/market/GENERAL.jsonl) for market-wide commentary. Both
     are filtered to a recent window (sw1.news.log.filter_recent_entries)
     before scoring, and the LLM itself judges which general-market
     entries are relevant to a given ticker. A ticker with nothing in
     window (or no key, or a scoring error) simply gets news_score=None --
     this must never fail the whole daily run over the news side of the
     score.

Output layout (all under data/, gitignored patterns updated to allow these):
  data/ohlcv/{TICKER}.csv        -- full daily OHLCV history (overwritten each run)
  data/indicators/{TICKER}.csv   -- OHLCV + computed indicator columns
  data/signals/latest.csv        -- one row per ticker: today's quant/news score snapshot
  data/signals/history.csv       -- append-only log of every run's snapshot (this is
                                     what SW2 will eventually read for paper-trading
                                     history once it exists)
  data/market/QQQ_indicators.csv -- market-wide (QQQ) OHLCV + indicators (2026-09-13:
                                     feeds sw1.market.regime for the market-regime filter)
  data/market/regime.json        -- today's market regime read (risk_on/risk_off + why)
  data/market/earnings_dates.json -- best-effort next-earnings date per M7 ticker
                                      (sw1.calendar.events), for SW2's event blackout
  data/news/cache.json           -- per-ticker {entries_hash, news_score, items} so an
                                     unchanged windowed view isn't re-billed every weekday

2026-09-25: `data/signals/latest.csv` also gets a `news_days_ago` column now
(see _collect_news_staleness below) -- a read-only staleness signal so the
dashboard can flag "N일간 뉴스 입력 없음" instead of the manual news-input
workflow silently going stale with no visible reminder. Purely diagnostic;
never affects news_score or any trading decision.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.calendar.events import fetch_next_earnings_date  # noqa: E402
from sw1.data.yahoo import M7_TICKERS, fetch_m7_snapshot, fetch_ohlcv  # noqa: E402
from sw1.indicators.technical import compute_all_technical_indicators  # noqa: E402
from sw1.market.regime import MARKET_INDEX_TICKER, compute_market_regime  # noqa: E402
from sw1.news.log import filter_recent_entries, latest_entry_date, read_log  # noqa: E402
from sw1.news.scorer import AnthropicKeyMissing, score_ticker_if_changed  # noqa: E402
from sw1.scoring.integrate import compute_quant_score  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
INDICATORS_DIR = DATA_DIR / "indicators"
SIGNALS_DIR = DATA_DIR / "signals"
MARKET_DIR = DATA_DIR / "market"
NEWS_INPUT_DIR = Path(__file__).resolve().parent.parent / "sw1" / "news" / "input"
NEWS_GENERAL_LOG_PATH = NEWS_INPUT_DIR / "market" / "GENERAL.jsonl"
NEWS_CACHE_PATH = DATA_DIR / "news" / "cache.json"


def _collect_market_regime() -> None:
    """Fetches the market index (QQQ) and writes both its indicator history
    and today's risk_on/risk_off read. Failures here must never take down
    the whole daily run -- sw1.market.regime already defaults to risk_on
    when it can't tell, and SW2 treats a missing regime.json the same way
    (see run_daily_paper_trading.py), so this degrades gracefully."""
    try:
        index_ohlcv = fetch_ohlcv(MARKET_INDEX_TICKER)
        index_indicators = compute_all_technical_indicators(index_ohlcv)
        index_indicators.to_csv(MARKET_DIR / f"{MARKET_INDEX_TICKER}_indicators.csv")

        regime = compute_market_regime(index_indicators, index_ticker=MARKET_INDEX_TICKER)
        (MARKET_DIR / "regime.json").write_text(
            json.dumps(
                {
                    "date": regime.date,
                    "regime": regime.regime,
                    "index_ticker": regime.index_ticker,
                    "close": regime.close,
                    "ma_50": regime.ma_50,
                    "ma_200": regime.ma_200,
                    "reason": regime.reason,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[market] {MARKET_INDEX_TICKER} regime={regime.regime} ({regime.reason})")
    except Exception as exc:  # noqa: BLE001 -- never fail the whole run over the market filter
        print(f"[WARN] could not compute market regime: {exc}")


def _collect_earnings_dates() -> None:
    """Best-effort forward-looking earnings date per ticker. See
    sw1.calendar.events.fetch_next_earnings_date -- returns None on any
    failure, which SW2's event-blackout check treats as "no earnings
    filter for this ticker today" rather than an error."""
    earnings_dates: dict[str, str | None] = {}
    for ticker in M7_TICKERS:
        earnings_dates[ticker] = fetch_next_earnings_date(ticker)
    (MARKET_DIR / "earnings_dates.json").write_text(
        json.dumps(earnings_dates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[market] earnings dates resolved for {sum(1 for v in earnings_dates.values() if v)}/{len(earnings_dates)} tickers")


def _collect_news_scores() -> dict[str, float | None]:
    """Reads each M7 ticker's accumulated commentary log
    (sw1/news/input/{TICKER}.jsonl) plus the shared ticker-agnostic
    market-wide log (sw1/news/input/market/GENERAL.jsonl), filters both to
    the recent window (sw1.news.log.filter_recent_entries), scores via
    sw1.news.scorer.score_ticker_if_changed (which reuses the cached score
    when the windowed view hasn't changed since the last run), and returns
    {ticker: news_score_or_None}.

    A ticker with nothing in window (own log empty AND no relevant-looking
    general entries -- the model itself decides relevance, so this
    function doesn't try to pre-filter), a missing ANTHROPIC_API_KEY, or
    any scoring error all resolve to None for that ticker -- this must
    never fail the whole daily run over the news side of the score (same
    graceful-degradation contract as _collect_market_regime/
    _collect_earnings_dates above)."""
    cache: dict[str, dict] = {}
    if NEWS_CACHE_PATH.exists():
        try:
            cache = json.loads(NEWS_CACHE_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] could not read news cache, starting fresh: {exc}")
            cache = {}

    today = datetime.now(timezone.utc).date()
    general_entries = filter_recent_entries(read_log(NEWS_GENERAL_LOG_PATH), as_of=today)

    news_scores: dict[str, float | None] = {}
    for ticker in M7_TICKERS:
        ticker_log_path = NEWS_INPUT_DIR / f"{ticker}.jsonl"
        ticker_entries = filter_recent_entries(read_log(ticker_log_path), as_of=today)
        try:
            entry, freshly_scored = score_ticker_if_changed(
                ticker, ticker_entries, general_entries, cached_entry=cache.get(ticker)
            )
            if entry is None:
                news_scores[ticker] = None
                continue
            cache[ticker] = entry
            news_scores[ticker] = entry["news_score"]
            status = "scored fresh" if freshly_scored else "reused cached score (window unchanged)"
            print(f"[news] {ticker} {status}: {entry['news_score']:.2f}")
        except AnthropicKeyMissing:
            print(f"[news] {ticker}: ANTHROPIC_API_KEY not set, skipping news score")
            news_scores[ticker] = None
        except Exception as exc:  # noqa: BLE001 -- news scoring must never fail the whole run
            print(f"[WARN] could not score news for {ticker}: {exc}")
            news_scores[ticker] = None

    NEWS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    NEWS_CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    return news_scores


def _collect_news_staleness(today: date) -> dict[str, int | None]:
    """2026-09-25: for each M7 ticker, how many days ago the most recent
    commentary entry was made -- from that ticker's own log OR the shared
    market-wide GENERAL log, whichever is more recent.

    Deliberately independent of _collect_news_scores() and of the 14-day
    scoring window: an entry that has aged out of the window and made
    news_score go back to None is exactly the case where 현우 most needs a
    "you haven't added news in a while" reminder, so this keeps counting
    past the window instead of resetting to "no data" the moment scoring
    stops using it.

    None means neither log has ever had a single parseable-date entry for
    that ticker (i.e. genuinely nothing has ever been submitted) -- the
    dashboard already has a separate "준비 중" state for that case and
    doesn't need a day count. Read-only / diagnostic, like
    scripts/run_historical_backtest.py's data_quality fields -- never
    affects any score or trading decision."""
    general_latest = latest_entry_date(read_log(NEWS_GENERAL_LOG_PATH))

    staleness: dict[str, int | None] = {}
    for ticker in M7_TICKERS:
        ticker_latest = latest_entry_date(read_log(NEWS_INPUT_DIR / f"{ticker}.jsonl"))
        candidates = [d for d in (ticker_latest, general_latest) if d is not None]
        staleness[ticker] = (today - max(candidates)).days if candidates else None
    return staleness


def main() -> int:
    for d in (OHLCV_DIR, INDICATORS_DIR, SIGNALS_DIR, MARKET_DIR):
        d.mkdir(parents=True, exist_ok=True)

    _collect_market_regime()
    _collect_earnings_dates()
    news_scores = _collect_news_scores()
    news_days_ago = _collect_news_staleness(datetime.now(timezone.utc).date())

    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    snapshot = fetch_m7_snapshot()
    failures = snapshot.pop("_failures", [])

    latest_rows = []
    for ticker, payload in snapshot.items():
        ohlcv: pd.DataFrame = payload["ohlcv"]
        fundamentals = payload["fundamentals"]

        ohlcv.to_csv(OHLCV_DIR / f"{ticker}.csv")

        indicators = compute_all_technical_indicators(ohlcv)
        indicators.to_csv(INDICATORS_DIR / f"{ticker}.csv")

        last_row = indicators.iloc[-1]
        quant_score = compute_quant_score(last_row)
        news_score = news_scores.get(ticker)

        latest_rows.append(
            {
                "run_ts": run_ts,
                "ticker": ticker,
                "date": indicators.index[-1].date().isoformat(),
                "close": ohlcv["Close"].iloc[-1],
                "per": fundamentals.per,
                "pbr": fundamentals.pbr,
                "rsi": last_row.get("RSI"),
                "macd_hist": last_row.get("MACD_HIST"),
                "bb_pct_b": last_row.get("BB_PCT_B"),
                "ma_cross": last_row.get("MA_CROSS"),
                "quant_score": quant_score,
                "news_score": news_score,  # None until sw1/news/input/{ticker}.txt has real commentary
                "news_days_ago": news_days_ago.get(ticker),  # None if never submitted; see _collect_news_staleness
            }
        )

    latest_df = pd.DataFrame(latest_rows)
    latest_df.to_csv(SIGNALS_DIR / "latest.csv", index=False)

    history_path = SIGNALS_DIR / "history.csv"
    latest_df.to_csv(history_path, mode="a", header=not history_path.exists(), index=False)

    print(f"[{run_ts}] collected {len(latest_rows)}/{len(latest_rows) + len(failures)} tickers")
    if failures:
        for ticker, err in failures:
            print(f"  [FAIL] {ticker}: {err}")
        # Partial success shouldn't fail the whole workflow run -- one bad
        # ticker on a given day is expected occasionally (rate limits etc).
        # Only fail hard if EVERYTHING failed.
        if not latest_rows:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

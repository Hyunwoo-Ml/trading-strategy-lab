"""
SW1 -- 신호 스크리닝(multi-timeframe signal screener) generation driver.

Runs on GitHub Actions right after scripts/collect_daily_data.py (needs
data/signals/latest.csv and data/indicators/{TICKER}.csv -- see
.github/workflows/collect-daily-data.yml). For every M7 ticker, this reuses
the already-computed daily quant_score/news_score (sw1.scoring.integrate)
together with that ticker's weekly trend (sw1.indicators.weekly -- the same
signal sw2.price_criteria_model already gates entries on) so 현우 can scan
every ticker's daily signal next to its weekly-timeframe confirmation in
one table, instead of reading each ticker's card individually.

A ticker's `confirmed` flag means a daily buy_1/buy_2 signal isn't fighting
the weekly trend (weekly_trend in {"up","flat"}) -- mirrors
sw2.price_criteria_model.PriceCriteriaModel's own
`ctx.weekly_trend == "down"` entry block. `diamond` is the strongest read
this screener can show: buy_2 (2차 매수 confirmed by both the weighted
consensus AND the single most-extreme indicator, see
sw1.scoring.integrate.compute_quant_score) together with a confirmed
weekly uptrend -- both timeframes agreeing in the same direction.

This is purely a display/screening aid (no new scoring weights, no new
thresholds -- it only reuses sw1.scoring.integrate.classify_signal's
existing thresholds and sw1.indicators.weekly's existing weekly-trend
read). It never feeds into any real trade decision; SW2's own models
remain the only source of truth for actual paper trades.

Output: data/screener/latest.csv (overwritten each run, one row per ticker)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.data.yahoo import M7_TICKERS  # noqa: E402
from sw1.indicators.weekly import compute_weekly_trend  # noqa: E402
from sw1.scoring.integrate import classify_signal, compute_integrated_score  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INDICATORS_DIR = DATA_DIR / "indicators"
SIGNALS_PATH = DATA_DIR / "signals" / "latest.csv"
SCREENER_DIR = DATA_DIR / "screener"

# 매매 신호 중 "진입 방향" 신호로 취급할 것들 -- confirmed/diamond 판정에만 쓰임
# (실제 매매 로직은 건드리지 않음, sw1.scoring.integrate.classify_signal 그대로 사용).
_BUY_SIGNALS = {"buy_1", "buy_2"}


def load_signals() -> pd.DataFrame:
    """data/signals/latest.csv, written by scripts/collect_daily_data.py.
    Missing/unreadable file degrades to an empty frame rather than
    raising -- same graceful-degradation contract as the rest of this
    pipeline (scripts/generate_price_criteria.py's load_news_scores)."""
    if not SIGNALS_PATH.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(SIGNALS_PATH)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()


def weekly_trend_for(ticker: str) -> tuple[str | None, str | None]:
    """Returns (trend, reason) from that ticker's own indicator history
    (data/indicators/{ticker}.csv). Any failure (missing file, too little
    history) degrades to (None, None) rather than raising -- the same
    file, read the same way, as scripts/run_daily_paper_trading.py's
    build_market_context."""
    indicators_path = INDICATORS_DIR / f"{ticker}.csv"
    if not indicators_path.exists():
        return None, None
    try:
        indicators_df = pd.read_csv(indicators_path, index_col=0, parse_dates=True)
        trend = compute_weekly_trend(indicators_df)
        return trend.trend, trend.reason
    except Exception as exc:  # noqa: BLE001
        print(f"[WARN] could not compute weekly trend for {ticker}: {exc}")
        return None, None


def main() -> int:
    signals_df = load_signals()
    if signals_df.empty:
        print(f"[WARN] no signals at {SIGNALS_PATH} -- run collect_daily_data.py first, nothing to screen")
        return 0

    signals_by_ticker = {row["ticker"]: row for _, row in signals_df.iterrows()}

    rows = []
    for ticker in M7_TICKERS:
        row = signals_by_ticker.get(ticker)
        if row is None:
            print(f"[WARN] no signal row for {ticker} -- skipping")
            continue

        quant_score = row.get("quant_score")
        if pd.isna(quant_score):
            print(f"[WARN] {ticker}: quant_score missing -- skipping")
            continue
        quant_score = float(quant_score)

        news_score_raw = row.get("news_score")
        # Missing news_score -> neutral 0.0, same convention
        # scripts/run_daily_paper_trading.py's score-threshold models use
        # (process_model_for_day) -- "no news this week" is neither
        # bullish nor bearish on its own.
        news_score = 0.0 if pd.isna(news_score_raw) else float(news_score_raw)

        integrated_score = compute_integrated_score(quant_score, news_score)
        trade_signal = classify_signal(integrated_score)

        weekly_trend, weekly_trend_reason = weekly_trend_for(ticker)

        confirmed = trade_signal.signal in _BUY_SIGNALS and weekly_trend in ("up", "flat")
        diamond = trade_signal.signal == "buy_2" and weekly_trend == "up"

        rows.append(
            {
                "ticker": ticker,
                "date": row.get("date"),
                "close": row.get("close"),
                "rsi": row.get("rsi"),
                "macd_hist": row.get("macd_hist"),
                "bb_pct_b": row.get("bb_pct_b"),
                "ma_cross": row.get("ma_cross"),
                "quant_score": quant_score,
                "news_score": None if pd.isna(news_score_raw) else news_score,
                "integrated_score": integrated_score,
                "signal": trade_signal.signal,
                "weekly_trend": weekly_trend,
                "weekly_trend_reason": weekly_trend_reason,
                "confirmed": confirmed,
                "diamond": diamond,
            }
        )

    if not rows:
        print("[WARN] no screener rows produced")
        return 1

    SCREENER_DIR.mkdir(parents=True, exist_ok=True)
    screener_df = pd.DataFrame(rows)
    screener_df.to_csv(SCREENER_DIR / "latest.csv", index=False)
    print(
        f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
        f"wrote screener rows for {len(rows)}/{len(M7_TICKERS)} tickers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

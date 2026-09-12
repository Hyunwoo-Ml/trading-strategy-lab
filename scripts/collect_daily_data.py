"""
SW1 -- daily data collection driver.

Meant to run on a GitHub Actions runner (scheduled via
.github/workflows/collect-daily-data.yml), NOT in the Cowork cloud sandbox
(blocked by org egress policy -- see sw1/data/yahoo.py docstring).

For each M7 ticker this:
  1. Fetches OHLCV + fundamentals via sw1.data.yahoo.fetch_m7_snapshot
  2. Computes technical indicators via sw1.indicators.technical
  3. Computes a quant-only score via sw1.scoring.integrate.compute_quant_score
     (news score isn't available yet -- ANTHROPIC_API_KEY pending -- so this
     writes the quant score alone; sw1/news/scorer.py will supply the news
     side of compute_integrated_score once that key is registered)

Output layout (all under data/, gitignored patterns updated to allow these):
  data/ohlcv/{TICKER}.csv        -- full daily OHLCV history (overwritten each run)
  data/indicators/{TICKER}.csv   -- OHLCV + computed indicator columns
  data/signals/latest.csv        -- one row per ticker: today's quant score snapshot
  data/signals/history.csv       -- append-only log of every run's snapshot (this is
                                     what SW2 will eventually read for paper-trading
                                     history once it exists)
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.data.yahoo import fetch_m7_snapshot  # noqa: E402
from sw1.indicators.technical import compute_all_technical_indicators  # noqa: E402
from sw1.scoring.integrate import compute_quant_score  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OHLCV_DIR = DATA_DIR / "ohlcv"
INDICATORS_DIR = DATA_DIR / "indicators"
SIGNALS_DIR = DATA_DIR / "signals"


def main() -> int:
    for d in (OHLCV_DIR, INDICATORS_DIR, SIGNALS_DIR):
        d.mkdir(parents=True, exist_ok=True)

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
                "news_score": None,  # TODO: sw1/news/scorer.py once ANTHROPIC_API_KEY is set
                "integrated_score": None,  # requires news_score
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

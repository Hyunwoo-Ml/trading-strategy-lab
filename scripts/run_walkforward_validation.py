"""
SW1 -- walk-forward validation driver.

Meant to run on a GitHub Actions runner (network to Yahoo Finance required,
same constraint as scripts/collect_daily_data.py -- NOT the Cowork cloud
sandbox), on demand (workflow_dispatch) rather than on the daily schedule --
this is a periodic health-check of the quant scoring formula, not something
that needs to run every weekday.

For each M7 ticker, pulls ~2 years of daily OHLCV via yfinance, computes the
same technical indicators + quant_score the daily pipeline uses
(sw1.indicators.technical, sw1.scoring.integrate.compute_quant_score,
UNCHANGED -- this validates the existing formula, it doesn't fit a new one),
computes each day's forward N-day return, and runs the rolling-window
stability check in sw1.validation.walkforward against the whole history.

News sentiment (sw1.news.scorer) is NOT part of this backtest -- see
sw1/validation/walkforward.py's docstring for why (no historical commentary
archive exists to replay). The live daily pipeline already accumulates one
signals/history.csv row per ticker per trading day
(scripts/collect_daily_data.py), which DOES include news_score once enough
of it has built up -- a future session can walk-forward-validate the news
component (or the full integrated score) against that live history the same
way this script does against yfinance history, once there's enough of it to
say anything meaningful. Task noted in TASKS.md.

Output: data/walkforward/results.json -- one JSON file with per-ticker
window-by-window IC results plus per-ticker and pooled-across-tickers
summaries. Overwritten each run (this is a snapshot re-validation, not an
append-only log -- unlike data/signals/history.csv, there's no reason to
keep every past run's numbers once a fresh one exists for the same history).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.data.yahoo import M7_TICKERS, fetch_ohlcv  # noqa: E402
from sw1.indicators.technical import compute_all_technical_indicators  # noqa: E402
from sw1.scoring.integrate import compute_quant_score  # noqa: E402
from sw1.validation.walkforward import (  # noqa: E402
    WindowResult,
    compute_forward_returns,
    run_walkforward,
    summarize_walkforward,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_PATH = DATA_DIR / "walkforward" / "results.json"

FETCH_PERIOD = "2y"       # how much yfinance history to pull per ticker
HORIZON_DAYS = 5          # forward-return horizon (trading days) the score is checked against
WINDOW_DAYS = 180         # rolling window length (calendar days) -- ~6 months per window
STEP_DAYS = 30            # window step (calendar days) -- ~1 month between window starts
MIN_OBS_PER_WINDOW = 15   # a window with fewer trading days than this is skipped, not scored


def _quant_scores_for_ticker(ticker: str) -> tuple[pd.Series, pd.Series] | None:
    """Fetches history for `ticker` and returns (quant_score_series,
    forward_return_series) aligned by date, or None if the fetch/compute
    fails for this ticker (one bad ticker must not crash the whole run --
    same graceful-degradation contract as collect_daily_data.py)."""
    ohlcv = fetch_ohlcv(ticker, period=FETCH_PERIOD)
    indicators = compute_all_technical_indicators(ohlcv)
    scores = indicators.apply(compute_quant_score, axis=1)
    forward_returns = compute_forward_returns(ohlcv["Close"], horizon_days=HORIZON_DAYS)
    return scores, forward_returns


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    per_ticker: dict[str, dict] = {}
    all_results: list[WindowResult] = []
    failures: list[tuple[str, str]] = []

    for ticker in M7_TICKERS:
        try:
            scores, forward_returns = _quant_scores_for_ticker(ticker)
            results = run_walkforward(
                scores,
                forward_returns,
                window_days=WINDOW_DAYS,
                step_days=STEP_DAYS,
                min_obs=MIN_OBS_PER_WINDOW,
            )
            summary = summarize_walkforward(results)
            per_ticker[ticker] = {
                "summary": summary,
                "windows": [r.to_dict() for r in results],
            }
            all_results.extend(results)
            pct = summary["pct_positive"]
            pct_str = f"{pct:.0%}" if pct is not None else "n/a"
            print(
                f"[walkforward] {ticker}: {summary['n_windows_scored']}/{summary['n_windows_total']} "
                f"windows scored, mean IC={summary['mean_ic']}, pct_positive={pct_str}"
            )
        except Exception as exc:  # noqa: BLE001 -- one bad ticker shouldn't crash the whole run
            print(f"[WARN] walk-forward validation failed for {ticker}: {exc}")
            failures.append((ticker, str(exc)))

    overall = summarize_walkforward(all_results)

    output = {
        "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {
            "fetch_period": FETCH_PERIOD,
            "horizon_days": HORIZON_DAYS,
            "window_days": WINDOW_DAYS,
            "step_days": STEP_DAYS,
            "min_obs_per_window": MIN_OBS_PER_WINDOW,
            "scope": "quant_score only (technical indicators) -- news_score not included, see sw1/validation/walkforward.py",
        },
        "overall": overall,
        "tickers": per_ticker,
        "failures": failures,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"[walkforward] overall: {overall['n_windows_scored']}/{overall['n_windows_total']} windows scored "
        f"across {len(per_ticker)} tickers, pooled mean IC={overall['mean_ic']}"
    )
    if failures:
        for ticker, err in failures:
            print(f"  [FAIL] {ticker}: {err}")
        if not per_ticker:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
SW1 -- daily price-criteria generation driver.

Runs on GitHub Actions right after scripts/collect_daily_data.py (needs the
indicator history that script writes to data/indicators/{TICKER}.csv --
see .github/workflows/collect-daily-data.yml). For every user-defined model
config under sw1/config/price_criteria_models/*.json, and every M7 ticker,
this computes today's buy_1_price / buy_2_price / target_price via
sw1.criteria.generator and writes:

  data/sw1/price_criteria/{model_name}/latest.csv    -- overwritten each run
  data/sw1/price_criteria/{model_name}/history.csv   -- append-only log

scripts/run_daily_paper_trading.py reads the `latest.csv` files to decide
whether any ticker actually touched a model's price levels today.

2026-09-14 (dashboard visibility): each row also carries that ticker's
current news_score (straight from data/signals/latest.csv, same value the
score-threshold models and the price-criteria news gate already use), the
model's own min_news_score threshold, and a news_blocked flag computed the
same way sw2.price_criteria_model.PriceCriteriaModel._entry_block_reason
does -- purely for display (the dashboard can now show, per ticker, whether
today's news would currently hold back a *new* entry). This file never
decides trades itself; scripts/run_daily_paper_trading.py's own call into
PriceCriteriaModel.decide() remains the single source of truth for that.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.criteria.generator import generate_price_criteria, load_price_criteria_params  # noqa: E402
from sw1.data.yahoo import M7_TICKERS  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
INDICATORS_DIR = DATA_DIR / "indicators"
PRICE_CRITERIA_DIR = DATA_DIR / "sw1" / "price_criteria"
CONFIG_DIR = PROJECT_ROOT / "sw1" / "config" / "price_criteria_models"
SIGNALS_PATH = DATA_DIR / "signals" / "latest.csv"


def load_news_scores() -> dict[str, float | None]:
    """Same per-ticker news_score the score-threshold models and SW2's price-
    criteria news gate use (data/signals/latest.csv, written by
    scripts/collect_daily_data.py). Missing file/column simply means no
    news_score is available yet for any ticker -- degrades gracefully like
    the rest of this pipeline."""
    if not SIGNALS_PATH.exists():
        return {}
    try:
        signals_df = pd.read_csv(SIGNALS_PATH)
    except Exception:  # noqa: BLE001
        return {}
    if "news_score" not in signals_df.columns:
        return {}
    return {
        row["ticker"]: (None if pd.isna(row.get("news_score")) else float(row["news_score"]))
        for _, row in signals_df.iterrows()
    }


def main() -> int:
    params_list = load_price_criteria_params(CONFIG_DIR)
    if not params_list:
        print(f"[WARN] no model configs found under {CONFIG_DIR} -- nothing to generate")
        return 0

    news_scores = load_news_scores()

    total_written = 0
    for params in params_list:
        rows = []
        for ticker in M7_TICKERS:
            indicators_path = INDICATORS_DIR / f"{ticker}.csv"
            if not indicators_path.exists():
                print(f"[WARN] no indicators file at {indicators_path} -- run collect_daily_data.py first, skipping {ticker}")
                continue

            indicators_df = pd.read_csv(indicators_path, index_col=0, parse_dates=True)
            try:
                criteria = generate_price_criteria(ticker, indicators_df, params)
            except ValueError as exc:
                print(f"[WARN] could not generate criteria for {ticker}/{params.model_name}: {exc}")
                continue

            news_score = news_scores.get(ticker)
            news_blocked = (
                params.min_news_score is not None
                and news_score is not None
                and news_score < params.min_news_score
            )

            rows.append(
                {
                    "model_name": criteria.model_name,
                    "ticker": criteria.ticker,
                    "date": criteria.date,
                    "close": criteria.close,
                    "buy_1_price": criteria.buy_1_price,
                    "buy_2_price": criteria.buy_2_price,
                    "stop_loss_pct": criteria.stop_loss_pct,
                    "target_price": criteria.target_price,
                    "basis_buy_1": criteria.basis.get("buy_1", ""),
                    "basis_buy_2": criteria.basis.get("buy_2", ""),
                    "basis_target": criteria.basis.get("target", ""),
                    "news_score": news_score,
                    "min_news_score": params.min_news_score,
                    "news_blocked": news_blocked,
                }
            )

        if not rows:
            print(f"[WARN] no criteria rows produced for model {params.model_name}")
            continue

        model_dir = PRICE_CRITERIA_DIR / params.model_name
        model_dir.mkdir(parents=True, exist_ok=True)

        latest_df = pd.DataFrame(rows)
        latest_df.to_csv(model_dir / "latest.csv", index=False)

        history_path = model_dir / "history.csv"
        latest_df.to_csv(history_path, mode="a", header=not history_path.exists(), index=False)

        print(f"[{params.model_name}] wrote criteria for {len(rows)}/{len(M7_TICKERS)} tickers")
        total_written += len(rows)

    if total_written == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

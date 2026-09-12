"""
SW2 -- daily paper trading driver.

Meant to run on a GitHub Actions runner AFTER scripts/collect_daily_data.py
(same pattern as SW1's daily collection -- see .github/workflows/
run-paper-trading.yml). For each registered model (sw2.models.default_registry)
this:

  1. Loads that model's persisted portfolio state (data/sw2/portfolios/{model}.json)
     -- or starts a fresh one with $100,000 paper cash if this is its first run.
  2. Reads today's signals from data/signals/latest.csv (SW1's output) and
     runs each ticker through the model's decide() to get a trade signal.
  3. Applies a simple, fixed-size position rule (see TRANCHE_FRACTION below)
     to the portfolio: buy_1 opens a position, buy_2 adds a second tranche,
     stop_loss/take_profit close it out, hold/caution do nothing.
  4. Marks the portfolio to market at today's close prices and appends to
     its equity curve.
  5. Persists the updated portfolio state and equity curve back to data/,
     then runs a pairwise statistical comparison (sw2.compare) across every
     model that has 2+ days of daily-return history and writes that to
     data/sw2/comparisons/latest.json.

Position sizing is intentionally simple for v1 -- a fixed dollar tranche,
not risk-adjusted or ticker-weighted. This is meant to get the whole
signal -> trade -> equity -> statistical-comparison loop running daily and
producing real comparison data; refine sizing once there's history to
react to.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw2.compare import compare_all_pairs  # noqa: E402
from sw2.ledger import Portfolio, Position, Trade  # noqa: E402
from sw2.models import TradingModel, default_registry  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SIGNALS_PATH = DATA_DIR / "signals" / "latest.csv"
PORTFOLIOS_DIR = DATA_DIR / "sw2" / "portfolios"
EQUITY_DIR = DATA_DIR / "sw2" / "equity"
COMPARISONS_DIR = DATA_DIR / "sw2" / "comparisons"

TRANCHE_FRACTION = 0.05  # 5% of starting cash per buy tranche -- v1 fixed sizing


def portfolio_to_dict(p: Portfolio) -> dict:
    return {
        "model_name": p.model_name,
        "starting_cash": p.starting_cash,
        "cash": p.cash,
        "positions": {ticker: asdict(pos) for ticker, pos in p.positions.items()},
        "trades": [asdict(t) for t in p.trades],
        "equity_curve": p.equity_curve,
    }


def portfolio_from_dict(d: dict) -> Portfolio:
    p = Portfolio(model_name=d["model_name"], starting_cash=d["starting_cash"])
    p.cash = d["cash"]
    p.positions = {ticker: Position(**pos) for ticker, pos in d["positions"].items()}
    p.trades = [Trade(**t) for t in d["trades"]]
    p.equity_curve = d["equity_curve"]
    return p


def load_or_create_portfolio(model_name: str, path: Path, starting_cash: float = 100_000.0) -> Portfolio:
    if path.exists():
        return portfolio_from_dict(json.loads(path.read_text(encoding="utf-8")))
    return Portfolio(model_name=model_name, starting_cash=starting_cash)


def save_portfolio(p: Portfolio, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(portfolio_to_dict(p), ensure_ascii=False, indent=2), encoding="utf-8")


def process_model_for_day(model: TradingModel, portfolio: Portfolio, signals_df: pd.DataFrame, run_date: str) -> None:
    tranche_dollars = portfolio.starting_cash * TRANCHE_FRACTION
    prices: dict[str, float] = {}

    for _, row in signals_df.iterrows():
        ticker = row["ticker"]
        price = float(row["close"])
        prices[ticker] = price

        quant_score = row.get("quant_score")
        if pd.isna(quant_score):
            continue
        news_score = row.get("news_score")
        news_score = 0.0 if pd.isna(news_score) else float(news_score)

        price_return = portfolio.price_return_from_entry(ticker, price)
        signal = model.decide(
            quant_score=float(quant_score),
            news_score=news_score,
            price_return_from_entry=price_return,
        )

        if signal.signal == "buy_1" and ticker not in portfolio.positions:
            portfolio.buy(run_date, ticker, price, tranche_dollars, "buy_1")
        elif signal.signal == "buy_2" and ticker in portfolio.positions:
            portfolio.buy(run_date, ticker, price, tranche_dollars, "buy_2")
        elif signal.signal in ("stop_loss", "take_profit") and ticker in portfolio.positions:
            portfolio.sell_all(run_date, ticker, price, signal.signal)
        # hold / caution -> no action in v1

    portfolio.mark_to_market(run_date, prices)


def main() -> int:
    if not SIGNALS_PATH.exists():
        print(f"[ERROR] no signals file at {SIGNALS_PATH} -- run collect_daily_data.py first")
        return 1

    signals_df = pd.read_csv(SIGNALS_PATH)
    run_date = datetime.now(timezone.utc).date().isoformat()

    registry = default_registry()
    returns_by_model: dict[str, pd.Series] = {}

    for model in registry.all():
        portfolio_path = PORTFOLIOS_DIR / f"{model.name}.json"
        portfolio = load_or_create_portfolio(model.name, portfolio_path)

        process_model_for_day(model, portfolio, signals_df, run_date)

        save_portfolio(portfolio, portfolio_path)

        equity_df = portfolio.equity_df()
        equity_path = EQUITY_DIR / f"{model.name}.csv"
        equity_path.parent.mkdir(parents=True, exist_ok=True)
        equity_df.to_csv(equity_path)
        returns_by_model[model.name] = (
            equity_df["daily_return"] if "daily_return" in equity_df else pd.Series(dtype=float)
        )

        latest_equity = portfolio.equity_curve[-1]["equity"] if portfolio.equity_curve else portfolio.cash
        print(
            f"[{model.name}] equity={latest_equity:.2f} cash={portfolio.cash:.2f} "
            f"positions={list(portfolio.positions.keys())}"
        )

    comparisons = compare_all_pairs(returns_by_model)
    comparisons_path = COMPARISONS_DIR / "latest.json"
    comparisons_path.parent.mkdir(parents=True, exist_ok=True)
    comparisons_path.write_text(
        json.dumps(
            {"run_date": run_date, "comparisons": [asdict(c) for c in comparisons]},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"[{run_date}] wrote {len(comparisons)} pairwise comparisons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


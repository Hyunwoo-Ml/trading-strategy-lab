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
  3. Applies a risk-based position rule (sw2.sizing.risk_based_tranche_dollars,
     see Task #22) to the portfolio: buy_1 opens a position, buy_2 adds a
     second tranche, stop_loss/take_profit close it out, hold/caution do
     nothing.
  4. Marks the portfolio to market at today's close prices and appends to
     its equity curve.
  5. Persists the updated portfolio state and equity curve back to data/,
     then runs a pairwise statistical comparison (sw2.compare) across every
     model that has 2+ days of daily-return history and writes that to
     data/sw2/comparisons/latest.json.

Position sizing (Task #22, 2026-09-14): each buy tranche's dollar amount
comes from sw2.sizing.risk_based_tranche_dollars, which sizes the tranche
so a stop-loss trigger loses roughly a fixed fraction of starting cash
regardless of how tight or wide that model/ticker's stop is, clamped to a
sane [min, max] fraction of starting cash. This replaced the old v1 fixed
5%-of-starting-cash tranche (TRANCHE_FRACTION) now that the signal ->
trade -> equity -> statistical-comparison loop has real history to size
against.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.calendar.events import is_event_blackout  # noqa: E402
from sw1.criteria.generator import PriceCriteria, load_price_criteria_params  # noqa: E402
from sw1.indicators.weekly import compute_weekly_trend  # noqa: E402
from sw2.compare import compare_all_pairs  # noqa: E402
from sw2.ledger import Portfolio, Position, Trade  # noqa: E402
from sw2.models import TradingModel, default_registry  # noqa: E402
from sw2.price_criteria_model import MarketContext, PriceCriteriaModel  # noqa: E402
from sw2.sizing import risk_based_tranche_dollars  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SIGNALS_PATH = DATA_DIR / "signals" / "latest.csv"
PRICE_CRITERIA_DIR = DATA_DIR / "sw1" / "price_criteria"
PRICE_CRITERIA_CONFIG_DIR = Path(__file__).resolve().parent.parent / "sw1" / "config" / "price_criteria_models"
PORTFOLIOS_DIR = DATA_DIR / "sw2" / "portfolios"
EQUITY_DIR = DATA_DIR / "sw2" / "equity"
COMPARISONS_DIR = DATA_DIR / "sw2" / "comparisons"
INDICATORS_DIR = DATA_DIR / "indicators"
MARKET_DIR = DATA_DIR / "market"

def load_market_regime() -> str | None:
    """Reads today's market regime written by collect_daily_data.py. Missing
    file (e.g. an older data snapshot, or the fetch failed that day) simply
    means no market-regime filter is applied -- same graceful-degradation
    pattern as the rest of this pipeline (a bad ticker doesn't crash the
    whole run, per the module docstring)."""
    path = MARKET_DIR / "regime.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("regime")
    except Exception:  # noqa: BLE001
        return None


def load_earnings_dates() -> dict[str, str | None]:
    path = MARKET_DIR / "earnings_dates.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def build_market_context(
    ticker: str,
    run_date: str,
    market_regime: str | None,
    earnings_dates: dict[str, str | None],
    news_scores: dict[str, float | None] | None = None,
) -> MarketContext:
    """Assembles the real-time confirmation context (sw2.price_criteria_model.
    MarketContext) for one ticker/day: volume confirmation and weekly trend
    come straight from that ticker's own indicator history
    (data/indicators/{ticker}.csv, already collected daily); market regime
    and earnings dates come from collect_daily_data.py's market/ outputs.
    Any missing piece degrades to "no filter" rather than raising, so one
    bad ticker's indicator file never takes down the whole daily run.

    2026-09-14: `news_scores` (ticker -> news_score, straight from
    data/signals/latest.csv -- the same per-ticker news_score the
    score-threshold models already blend in) is optional and defaults to
    None/empty so existing callers see no change; a ticker missing from it
    simply gets news_score=None, same as "no news this week" everywhere
    else in the pipeline."""
    volume_ratio: float | None = None
    weekly_trend: str | None = None

    indicators_path = INDICATORS_DIR / f"{ticker}.csv"
    if indicators_path.exists():
        try:
            indicators_df = pd.read_csv(indicators_path, index_col=0, parse_dates=True)
            last = indicators_df.iloc[-1]
            vol_ratio_value = last.get("VOL_RATIO")
            if vol_ratio_value is not None and pd.notna(vol_ratio_value):
                volume_ratio = float(vol_ratio_value)
            weekly_trend = compute_weekly_trend(indicators_df).trend
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] could not build market context for {ticker}: {exc}")

    blackout = is_event_blackout(run_date, earnings_date=earnings_dates.get(ticker))

    news_score = (news_scores or {}).get(ticker)

    return MarketContext(
        volume_ratio=volume_ratio,
        weekly_trend=weekly_trend,
        market_regime=market_regime,
        event_blackout=blackout.is_blackout,
        event_reasons=blackout.reasons,
        news_score=news_score,
    )


def portfolio_to_dict(p: Portfolio) -> dict:
    return {
        "model_name": p.model_name,
        "starting_cash": p.starting_cash,
        "transaction_cost_pct": p.transaction_cost_pct,
        "cash": p.cash,
        "positions": {ticker: asdict(pos) for ticker, pos in p.positions.items()},
        "trades": [asdict(t) for t in p.trades],
        "equity_curve": p.equity_curve,
    }


def portfolio_from_dict(d: dict) -> Portfolio:
    # transaction_cost_pct is missing on portfolios saved before that field
    # existed -- fall back to the Portfolio class default (see sw2/ledger.py)
    # rather than raising, same graceful-degradation pattern as the rest of
    # this pipeline.
    kwargs = {"model_name": d["model_name"], "starting_cash": d["starting_cash"]}
    if "transaction_cost_pct" in d:
        kwargs["transaction_cost_pct"] = d["transaction_cost_pct"]
    p = Portfolio(**kwargs)
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
    # model.thresholds.stop_loss_pct is fixed per model (not per ticker/day),
    # so the risk-based tranche size can be computed once for this whole run
    # -- see sw2.sizing and Task #22.
    tranche_dollars = risk_based_tranche_dollars(portfolio.starting_cash, model.thresholds.stop_loss_pct)
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
        elif (
            signal.signal == "buy_2"
            and ticker in portfolio.positions
            and portfolio.positions[ticker].tranche_count < 2
        ):
            portfolio.buy(run_date, ticker, price, tranche_dollars, "buy_2")
        elif signal.signal in ("stop_loss", "take_profit") and ticker in portfolio.positions:
            portfolio.sell_all(run_date, ticker, price, signal.signal)
        # hold / caution -> no action in v1

    portfolio.mark_to_market(run_date, prices)


def process_price_criteria_model_for_day(
    model: PriceCriteriaModel,
    portfolio: Portfolio,
    criteria_df: pd.DataFrame,
    run_date: str,
    market_regime: str | None,
    earnings_dates: dict[str, str | None],
    news_scores: dict[str, float | None] | None = None,
) -> None:
    """Same trade-application shape as process_model_for_day, but decisions
    come from SW1's exported price levels (sw1.criteria.generator) instead
    of an abstract score -- see sw2/price_criteria_model.py. A model that
    finds no ticker touching its levels today simply does nothing, by
    design (per feedback: "매일 매매하는 게 아니라 기준이 닿아 있을 때만").

    2026-09-13: a MarketContext (volume/weekly-trend/market-regime/event
    blackout) is built per ticker and passed into decide() so a price-level
    touch only becomes a real buy_1/buy_2 when it's confirmed -- see
    build_market_context() and sw2.price_criteria_model.MarketContext.

    2026-09-14 (Task #22): unlike process_model_for_day, this model's
    stop_loss_pct comes from SW1's per-ticker/day price criteria
    (criteria.stop_loss_pct), not a fixed per-model constant -- so the
    risk-based tranche size is recomputed per row instead of once per run.

    2026-09-14 (news gate): `news_scores` (ticker -> news_score) is passed
    straight through to build_market_context() so a model with
    PriceCriteriaParams.min_news_score set can also hold back a new entry
    on bad news -- see sw2.price_criteria_model.PriceCriteriaModel.
    Optional/defaults to None so existing callers/tests are unaffected.
    """
    prices: dict[str, float] = {}

    for _, row in criteria_df.iterrows():
        ticker = row["ticker"]
        price = float(row["close"])
        prices[ticker] = price

        criteria = PriceCriteria(
            model_name=row["model_name"],
            ticker=ticker,
            date=row["date"],
            close=price,
            buy_1_price=float(row["buy_1_price"]),
            buy_2_price=float(row["buy_2_price"]),
            stop_loss_pct=float(row["stop_loss_pct"]),
            target_price=float(row["target_price"]),
            basis={
                "buy_1": row.get("basis_buy_1", ""),
                "buy_2": row.get("basis_buy_2", ""),
                "target": row.get("basis_target", ""),
            },
        )

        existing_position = portfolio.positions.get(ticker)
        is_holding = existing_position is not None
        tranche_count = existing_position.tranche_count if existing_position else 0
        price_return = portfolio.price_return_from_entry(ticker, price)
        market_context = build_market_context(ticker, run_date, market_regime, earnings_dates, news_scores)
        tranche_dollars = risk_based_tranche_dollars(portfolio.starting_cash, criteria.stop_loss_pct)

        signal = model.decide(
            criteria=criteria,
            is_holding=is_holding,
            tranche_count=tranche_count,
            price_return_from_entry=price_return,
            market_context=market_context,
        )

        if signal.signal == "buy_1" and ticker not in portfolio.positions:
            portfolio.buy(run_date, ticker, price, tranche_dollars, "buy_1")
        elif signal.signal == "buy_2" and ticker in portfolio.positions and tranche_count < 2:
            portfolio.buy(run_date, ticker, price, tranche_dollars, "buy_2")
        elif signal.signal in ("stop_loss", "take_profit") and ticker in portfolio.positions:
            portfolio.sell_all(run_date, ticker, price, signal.signal)
        # hold -> no action

    portfolio.mark_to_market(run_date, prices)


def _finalize_model_run(model_name: str, portfolio: Portfolio, returns_by_model: dict[str, pd.Series]) -> None:
    """Shared save/report tail for both model types -- persist portfolio
    state, write the equity CSV the dashboard reads, and stash daily
    returns for the cross-model statistical comparison below."""
    portfolio_path = PORTFOLIOS_DIR / f"{model_name}.json"
    save_portfolio(portfolio, portfolio_path)

    equity_df = portfolio.equity_df()
    equity_path = EQUITY_DIR / f"{model_name}.csv"
    equity_path.parent.mkdir(parents=True, exist_ok=True)
    equity_df.to_csv(equity_path)
    returns_by_model[model_name] = (
        equity_df["daily_return"] if "daily_return" in equity_df else pd.Series(dtype=float)
    )

    latest_equity = portfolio.equity_curve[-1]["equity"] if portfolio.equity_curve else portfolio.cash
    print(
        f"[{model_name}] equity={latest_equity:.2f} cash={portfolio.cash:.2f} "
        f"positions={list(portfolio.positions.keys())}"
    )


def main() -> int:
    if not SIGNALS_PATH.exists():
        print(f"[ERROR] no signals file at {SIGNALS_PATH} -- run collect_daily_data.py first")
        return 1

    signals_df = pd.read_csv(SIGNALS_PATH)
    run_date = datetime.now(timezone.utc).date().isoformat()

    registry = default_registry()
    returns_by_model: dict[str, pd.Series] = {}
    market_regime = load_market_regime()
    earnings_dates = load_earnings_dates()
    # Same per-ticker news_score the score-threshold models already blend
    # into their integrated score (see process_model_for_day below) --
    # reused here so a price-criteria model can opt into a news gate too
    # (PriceCriteriaParams.min_news_score, see sw2/price_criteria_model.py).
    news_scores: dict[str, float | None] = {
        row["ticker"]: (None if pd.isna(row.get("news_score")) else float(row["news_score"]))
        for _, row in signals_df.iterrows()
    }
    print(f"[market] regime={market_regime or 'unknown (no filter applied)'}")

    # -- score-threshold models (sw1.scoring.integrate) -----------------
    for model in registry.all():
        portfolio = load_or_create_portfolio(model.name, PORTFOLIOS_DIR / f"{model.name}.json")
        process_model_for_day(model, portfolio, signals_df, run_date)
        _finalize_model_run(model.name, portfolio, returns_by_model)

    # -- user-defined price-criteria models (sw1.criteria.generator) ----
    for params in load_price_criteria_params(PRICE_CRITERIA_CONFIG_DIR):
        model = PriceCriteriaModel(params=params)
        criteria_path = PRICE_CRITERIA_DIR / model.name / "latest.csv"
        if not criteria_path.exists():
            print(f"[WARN] no price-criteria file at {criteria_path} for model {model.name} -- run scripts/generate_price_criteria.py first, skipping")
            continue
        criteria_df = pd.read_csv(criteria_path)

        portfolio = load_or_create_portfolio(model.name, PORTFOLIOS_DIR / f"{model.name}.json")
        process_price_criteria_model_for_day(
            model, portfolio, criteria_df, run_date, market_regime, earnings_dates, news_scores
        )
        _finalize_model_run(model.name, portfolio, returns_by_model)

    comparisons = compare_all_pairs(returns_by_model)
    comparisons_path = COMPARISONS_DIR / "latest.json"
    comparisons_path.parent.mkdir(parents=True, exist_ok=True)
    # allow_nan=False is deliberate: sw2.compare already turns an undefined
    # (zero-variance) t-test into None/null rather than NaN, but this is a
    # second line of defense -- a bare `NaN` token is invalid JSON and
    # silently breaks the dashboard's JSON.parse, so if some future metric
    # ever produces a real NaN this must fail the run loudly instead of
    # writing broken JSON that the dashboard can't read.
    comparisons_path.write_text(
        json.dumps(
            {"run_date": run_date, "comparisons": [asdict(c) for c in comparisons]},
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    print(f"[{run_date}] wrote {len(comparisons)} pairwise comparisons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

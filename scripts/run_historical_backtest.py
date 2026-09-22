"""
SW2 -- long-horizon historical backtest of the CURRENTLY REGISTERED trading
models (2026-09-16 user request: "정해진 모델 별 기준을 과거 3년 데이터에
등록했을 때, 구간(기간) 별 수익률이 어느정도인지 보여주는 직관적 지표").

This answers a different question than sw1/validation/walkforward.py's
rolling-window IC check (see that module's docstring). Walk-forward asks
"does the quant_score FORMULA have genuine predictive power". This script
asks "if these five models' buy/sell/stop-loss RULES had actually been
trading over the last ~3 years, what would the realized return have been,
broken down quarter by quarter" -- a real simulated trade-by-trade replay,
not a correlation check.

Meant to run on a GitHub Actions runner (network to Yahoo Finance
required, same constraint as scripts/collect_daily_data.py and
scripts/run_walkforward_validation.py -- NOT the Cowork cloud sandbox), on
demand (workflow_dispatch) since this is a periodic health-check, not
something that needs to run every weekday. Entirely separate from the live
daily paper-trading state (data/sw2/portfolios/*.json) -- this never reads
or writes that; it starts five FRESH $100,000 portfolios and replays them
against history, so live paper-trading progress is completely unaffected.

Design: reuses the exact same decision/ledger code the live daily pipeline
uses wherever the two workloads share a data shape, so this can never
silently diverge from what actually runs live:
  - score-threshold models (baseline/technical_only/conservative):
    scripts.run_daily_paper_trading.process_model_for_day, called once per
    historical trading day with that day's signals -- unmodified.
  - price-criteria models (price_model_1/price_model_2):
    sw2.price_criteria_model.PriceCriteriaModel.decide() and
    sw2.ledger.Portfolio.buy/sell_all/mark_to_market are reused directly,
    but the day-orchestration (_apply_price_criteria_day below) is its own
    function rather than run_daily_paper_trading.process_price_criteria_
    model_for_day, because that function's MarketContext comes from
    reading data/indicators/{TICKER}.csv -- today's LIVE file, which is
    wrong for a day three years ago. Here MarketContext is built from the
    in-memory historical indicator/weekly-trend/regime series instead
    (_market_context_for_day). The trade-application branch (buy_1/buy_2/
    stop_loss/take_profit -> Portfolio calls) is copied verbatim from that
    function to keep the two paths behaviorally identical.
  - sw1.criteria.generator.generate_price_criteria is called once per
    ticker per day on that day's indicator history SLICE (indicators up to
    and including that day, never later rows) -- the same "recompute from
    whatever's known as of today" shape the live pipeline uses, just
    replayed day by day instead of once.
  - benchmark buy-and-hold references (SPY/QQQ/TLT, added 2026-09-22 user
    request -- see BENCHMARK_TICKERS below): NOT trading models at all, no
    decide()/Portfolio involved. Just "buy the whole $100,000 on day one
    and never touch it again", bucketed through the exact same
    sw1.validation.backtest helpers so the output shape (and therefore the
    dashboard rendering code) is identical to a real model's.

SCOPE / LIMITATIONS (also written into the output JSON's config.scope_notes
so the dashboard can show them next to the numbers):
  - No historical news archive exists (see sw1/validation/walkforward.py's
    docstring for the same limitation) -- news_score is None for the whole
    backtest, so news-weighted models trade on technical indicators alone
    during this period.
  - Earnings-date blackout is not applied -- yfinance's earnings-date API
    is forward-looking only (sw1.calendar.events.fetch_next_earnings_date),
    there is no reliable multi-year historical archive to replay.
  - FOMC/CPI macro blackout dates (sw1.calendar.events.MACRO_EVENT_DATES)
    are hand-entered for 2023-2026 (2026-09-22: extended back from
    2026-only to cover this script's full 3-year FETCH_PERIOD window), so
    this filter is applied faithfully across the whole replay -- add a new
    year's dates there once that year's schedules are published if
    FETCH_PERIOD is ever widened past 2023.
  - Volume confirmation, weekly-trend confirmation, market regime (QQQ),
    transaction costs (10bps), and risk-based position sizing ARE all
    replayed faithfully against real historical data, identically to the
    live pipeline's rules.
  - Benchmark buy-and-hold references carry NONE of the above rules (no
    stop-loss, no transaction costs, no position sizing) -- they're a pure
    "what if I'd just bought and never sold" reference line, not another
    trading model.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from sw1.calendar.events import is_event_blackout  # noqa: E402
from sw1.criteria.generator import PriceCriteria, PriceCriteriaParams, generate_price_criteria, load_price_criteria_params  # noqa: E402
from sw1.data.yahoo import M7_TICKERS, fetch_ohlcv  # noqa: E402
from sw1.indicators.technical import compute_all_technical_indicators  # noqa: E402
from sw1.indicators.weekly import resample_to_weekly  # noqa: E402
from sw1.market.regime import MARKET_INDEX_TICKER  # noqa: E402
from sw1.scoring.integrate import compute_quant_score  # noqa: E402
from sw1.validation.backtest import bucket_equity_by_period, total_return  # noqa: E402
from sw2.ledger import Portfolio  # noqa: E402
from sw2.models import default_registry  # noqa: E402
from sw2.price_criteria_model import MarketContext, PriceCriteriaModel  # noqa: E402
from sw2.sizing import risk_based_tranche_dollars  # noqa: E402

import run_daily_paper_trading as daily_script  # noqa: E402 -- reuse the live score-threshold day-driver verbatim

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
PRICE_CRITERIA_CONFIG_DIR = Path(__file__).resolve().parent.parent / "sw1" / "config" / "price_criteria_models"
OUTPUT_PATH = DATA_DIR / "backtest" / "results.json"

FETCH_PERIOD = "3y"       # how much yfinance history to pull per ticker
PERIOD_FREQ = "Q"         # quarterly buckets (2026-09-16 user choice)
STARTING_CASH = 100_000.0
WEEKLY_SHORT_WINDOW = 10  # weeks -- matches sw1.indicators.weekly's own default
WEEKLY_LONG_WINDOW = 30   # weeks

# 2026-09-22 user request ("SPY 장기 보유, QQQ 장기 보유, 국채 30년물 장기
# 보유와도 비교하고 싶다"): pure buy-and-hold reference lines, computed with
# the SAME fetch window/starting cash as the five trading models so the
# comparison is apples-to-apples. TLT (iShares 20+ Year Treasury Bond ETF)
# stands in for "30년물 국채 장기 보유" -- there is no investable instrument
# that IS the raw 30-year Treasury bond with dividends/coupons reinvested,
# and yfinance has no clean total-return series for it either, so TLT's
# quoted price (which yfinance auto-adjusts for distributions) is the
# closest practical proxy for "long-duration Treasury exposure, held". A
# literal 30y zero-coupon or a hand-rolled coupon-reinvestment model would
# be a lot more machinery for a reference line that's meant to answer one
# question -- "did the actual strategies beat just parking the money" --
# so TLT was judged close enough for that purpose; see the matching
# scope_notes entry below for the same caveat surfaced on the dashboard.
BENCHMARK_TICKERS: dict[str, str] = {
    "SPY": "S&P 500 (SPY) 장기 보유",
    "QQQ": "나스닥 100 (QQQ) 장기 보유",
    "TLT": "20년+ 국채 ETF (TLT, 30년물 장기채 대용) 장기 보유",
}

SCOPE_NOTES = [
    "뉴스 감성 점수는 과거 데이터가 없어 이 백테스트 전 기간 동안 중립(미반영)으로 처리됩니다 -- "
    "뉴스 비중이 있는 모델(baseline 등)도 이 기간에는 사실상 기술적 지표만으로 판단합니다.",
    "실적 발표일 블랙아웃은 yfinance가 과거 실적 발표일을 안정적으로 제공하지 않아 이 백테스트에는 "
    "반영되지 않습니다 (라이브 파이프라인에는 반영됨).",
    "거래량 확인, 주봉 추세, 시장 레짐(QQQ 기준), 거래비용(수수료+슬리피지 10bps), 리스크 기반 포지션 사이징, "
    "FOMC/CPI 매크로 이벤트 블랙아웃(2023~2026년 실제 발표일 기준)은 라이브 파이프라인과 동일한 규칙으로 "
    "전 기간에 반영됩니다.",
    "라이브 페이퍼 트레이딩 포트폴리오(data/sw2/portfolios/*.json)와는 완전히 별개 -- 이 백테스트는 "
    "$100,000 짜리 새 가상 포트폴리오 5개로 과거를 재생할 뿐, 실시간 진행 중인 매매 기록은 전혀 건드리지 않습니다.",
    "벤치마크(SPY/QQQ/TLT)는 매매 규칙이 전혀 없는 '기간 첫날 $100,000 전액 매수 후 그대로 보유'만 가정한 "
    "참고선입니다 -- yfinance가 자동 조정한 종가를 사용해 배당/분배금 재투자 효과를 근사치로 반영하며, "
    "거래비용·리밸런싱·손절은 전혀 적용하지 않습니다. TLT(iShares 20년+ 국채 ETF)는 만기 30년물 국채 그 "
    "자체가 아니라 장기 국채 익스포저에 대한 실용적 대용치입니다.",
]


def _weekly_trend_series(ohlcv: pd.DataFrame) -> pd.Series:
    """Same up/down/flat rule as sw1.indicators.weekly.compute_weekly_trend,
    computed for every weekly bar in the history (not just the latest one)
    and mapped back onto daily dates via a backward as-of join, so a given
    daily date only ever sees the most recently COMPLETED weekly bar --
    never the still-forming current week (that would leak future
    information into a historical backtest)."""
    weekly = resample_to_weekly(ohlcv).copy()
    short_col, long_col = f"MA_{WEEKLY_SHORT_WINDOW}", f"MA_{WEEKLY_LONG_WINDOW}"
    weekly[short_col] = weekly["Close"].rolling(WEEKLY_SHORT_WINDOW, min_periods=WEEKLY_SHORT_WINDOW).mean()
    weekly[long_col] = weekly["Close"].rolling(WEEKLY_LONG_WINDOW, min_periods=WEEKLY_LONG_WINDOW).mean()

    def _trend(row: pd.Series) -> str:
        ms, ml, close = row.get(short_col), row.get(long_col), row["Close"]
        if pd.isna(ms) or pd.isna(ml):
            return "flat"
        if close < ms < ml:
            return "down"
        if close > ms > ml:
            return "up"
        return "flat"

    weekly["trend"] = weekly.apply(_trend, axis=1)
    weekly_reset = weekly.reset_index()
    weekly_reset = weekly_reset.rename(columns={weekly_reset.columns[0]: "week_end"})
    daily_dates = pd.DataFrame({"date": ohlcv.index}).sort_values("date")
    merged = pd.merge_asof(daily_dates, weekly_reset[["week_end", "trend"]], left_on="date", right_on="week_end", direction="backward")
    return pd.Series(merged["trend"].values, index=merged["date"].values)


def _regime_series(qqq_indicators: pd.DataFrame) -> pd.Series:
    """Vectorized version of sw1.market.regime.compute_market_regime's rule
    (risk_off when close < MA_50 and MA_50 < MA_200, else risk_on --
    including its "not enough history yet" default of risk_on, since NaN
    comparisons evaluate to False here) applied to every historical row at
    once instead of just the latest one."""
    close, ma50, ma200 = qqq_indicators["Close"], qqq_indicators["MA_50"], qqq_indicators["MA_200"]
    risk_off = (close < ma50) & (ma50 < ma200)
    return risk_off.map({True: "risk_off", False: "risk_on"})


def _market_context_for_day(ticker: str, date, ticker_data: dict, regime_by_date: pd.Series) -> MarketContext:
    indicators = ticker_data[ticker]["indicators"]
    volume_ratio = None
    if date in indicators.index:
        vol_ratio_value = indicators.loc[date, "VOL_RATIO"]
        if pd.notna(vol_ratio_value):
            volume_ratio = float(vol_ratio_value)
    weekly_trend = ticker_data[ticker]["weekly_trend"].get(date)
    regime = regime_by_date.get(date, "risk_on")
    date_str = date.date().isoformat() if hasattr(date, "date") else str(date)
    blackout = is_event_blackout(date_str, earnings_date=None)
    return MarketContext(
        volume_ratio=volume_ratio,
        weekly_trend=weekly_trend,
        market_regime=regime,
        event_blackout=blackout.is_blackout,
        event_reasons=blackout.reasons,
        news_score=None,
    )


def _generate_criteria_rows_for_day(
    ticker_data: dict, date, price_criteria_params: list[PriceCriteriaParams]
) -> dict[str, list[dict]]:
    """One row per ticker per price-criteria model, generated from that
    ticker's indicator history SLICE up to and including `date` -- never
    later rows, so buy_1/buy_2/target for "today" never peeks at the
    future (the same no-lookahead property sw1.criteria.generator already
    has when called once per day live; this just calls it once per
    historical day instead)."""
    rows_by_model: dict[str, list[dict]] = {p.model_name: [] for p in price_criteria_params}
    for ticker, d in ticker_data.items():
        indicators = d["indicators"]
        if date not in indicators.index:
            continue
        # 2026-09-22: yfinance's 3y history occasionally has a NaN Close on
        # an isolated day (data-provider gap). Before the 1-day-lag fix,
        # price_model_1/2 never actually traded, so a NaN close was never
        # fed into Portfolio.buy()/mark_to_market() and this was never
        # exposed. Now that they do trade, a single NaN close would corrupt
        # that day's equity and, if it landed on a quarter's last trading
        # day, produce a NaN in the JSON output (json.dumps(allow_nan=False)
        # then crashes the whole run). Skip the ticker for this one day
        # instead -- same graceful-degradation pattern used everywhere else
        # in this pipeline.
        if pd.isna(indicators.loc[date, "Close"]):
            continue
        pos = indicators.index.get_loc(date)
        hist_slice = indicators.iloc[: pos + 1]
        for params in price_criteria_params:
            try:
                criteria = generate_price_criteria(ticker, hist_slice, params)
            except Exception:  # noqa: BLE001 -- one bad ticker/day must not crash the whole backtest
                continue
            if pd.isna(criteria.close) or pd.isna(criteria.buy_1_price) or pd.isna(criteria.buy_2_price) or pd.isna(criteria.target_price):
                continue
            rows_by_model[params.model_name].append(
                {
                    "ticker": ticker,
                    "close": criteria.close,
                    "buy_1_price": criteria.buy_1_price,
                    "buy_2_price": criteria.buy_2_price,
                    "stop_loss_pct": criteria.stop_loss_pct,
                    "target_price": criteria.target_price,
                }
            )
    return rows_by_model


def _apply_price_criteria_day(
    model: PriceCriteriaModel,
    portfolio: Portfolio,
    rows: list[dict],
    run_date: str,
    market_context_by_ticker: dict[str, MarketContext],
) -> None:
    """Trade-application branch copied verbatim from scripts.run_daily_
    paper_trading.process_price_criteria_model_for_day -- see this
    module's docstring for why the day-orchestration around it differs
    (MarketContext source)."""
    prices: dict[str, float] = {}
    for row in rows:
        ticker = row["ticker"]
        price = row["close"]
        prices[ticker] = price

        criteria = PriceCriteria(
            model_name=model.name,
            ticker=ticker,
            date=run_date,
            close=price,
            buy_1_price=row["buy_1_price"],
            buy_2_price=row["buy_2_price"],
            stop_loss_pct=row["stop_loss_pct"],
            target_price=row["target_price"],
        )

        existing_position = portfolio.positions.get(ticker)
        is_holding = existing_position is not None
        tranche_count = existing_position.tranche_count if existing_position else 0
        price_return = portfolio.price_return_from_entry(ticker, price)
        market_context = market_context_by_ticker.get(ticker, MarketContext())
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


def _config(period_freq: str) -> dict:
    return {
        "fetch_period": FETCH_PERIOD,
        "period_freq": period_freq,
        "starting_cash": STARTING_CASH,
        "scope_notes": SCOPE_NOTES,
    }


def _buy_and_hold_payload(ohlcv: pd.DataFrame, label: str, period_freq: str) -> dict:
    """Turns a raw OHLCV history into the same {starting_cash, final_equity,
    total_return, n_trades, n_trading_days, periods} shape a real model
    produces, so the dashboard's existing per-model rendering code can
    treat a benchmark exactly like a model without a parallel code path.
    n_trades is always 1 -- the single day-one purchase -- there is no
    selling, rebalancing, or re-entry.

    2026-09-22: a benchmark's Close can carry the same isolated NaN gaps a
    stock ticker's can (see _generate_criteria_rows_for_day's docstring for
    the live-pipeline equivalent) -- dropping those rows before computing
    equity is safe here because equity is just close price * a FIXED share
    count, so a missing day is simply absent from the curve rather than
    corrupting it.
    """
    close = ohlcv["Close"].dropna()
    if close.empty:
        raise RuntimeError("no usable Close prices for buy-and-hold benchmark")
    shares = STARTING_CASH / float(close.iloc[0])
    equity_df = pd.DataFrame({"equity": close * shares})
    periods = bucket_equity_by_period(equity_df, freq=period_freq)
    return {
        "label": label,
        "starting_cash": STARTING_CASH,
        "final_equity": float(equity_df["equity"].iloc[-1]),
        "total_return": total_return(equity_df),
        "n_trades": 1,
        "n_trading_days": len(equity_df),
        "periods": [p.to_dict() for p in periods],
    }


def run_backtest(period_freq: str = PERIOD_FREQ) -> dict:
    ticker_data: dict[str, dict] = {}
    failures: list[tuple[str, str]] = []

    for ticker in M7_TICKERS:
        try:
            ohlcv = fetch_ohlcv(ticker, period=FETCH_PERIOD)
            indicators = compute_all_technical_indicators(ohlcv)
            quant_scores = indicators.apply(compute_quant_score, axis=1)
            weekly_trend = _weekly_trend_series(ohlcv)
            ticker_data[ticker] = {
                "ohlcv": ohlcv,
                "indicators": indicators,
                "quant_scores": quant_scores,
                "weekly_trend": weekly_trend,
            }
        except Exception as exc:  # noqa: BLE001 -- one bad ticker shouldn't crash the whole run
            failures.append((ticker, str(exc)))

    if not ticker_data:
        return {
            "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "config": _config(period_freq),
            "models": {},
            "benchmarks": {},
            "failures": failures,
        }

    qqq_ohlcv = pd.DataFrame()
    try:
        qqq_ohlcv = fetch_ohlcv(MARKET_INDEX_TICKER, period=FETCH_PERIOD)
        qqq_indicators = compute_all_technical_indicators(qqq_ohlcv)
        regime_by_date = _regime_series(qqq_indicators)
    except Exception as exc:  # noqa: BLE001 -- missing market regime just means "no filter", not a crash
        failures.append((MARKET_INDEX_TICKER, str(exc)))
        regime_by_date = pd.Series(dtype=object)

    all_dates = sorted(set().union(*[d["indicators"].index for d in ticker_data.values()]))

    registry = default_registry()
    score_portfolios = {m.name: Portfolio(model_name=m.name, starting_cash=STARTING_CASH) for m in registry.all()}

    price_criteria_params = load_price_criteria_params(PRICE_CRITERIA_CONFIG_DIR)
    pc_models = {p.model_name: PriceCriteriaModel(params=p) for p in price_criteria_params}
    pc_portfolios = {name: Portfolio(model_name=name, starting_cash=STARTING_CASH) for name in pc_models}

    for date in all_dates:
        date_str = date.date().isoformat() if hasattr(date, "date") else str(date)

        # -- score-threshold models (baseline / technical_only / conservative) --
        rows = []
        for ticker, d in ticker_data.items():
            if date not in d["indicators"].index:
                continue
            qs = d["quant_scores"].loc[date]
            close_value = d["ohlcv"].loc[date, "Close"]
            if pd.isna(qs) or pd.isna(close_value):
                # 2026-09-22: an isolated NaN Close (yfinance data gap) must
                # never reach Portfolio.buy()/mark_to_market() -- see the
                # matching guard in _generate_criteria_rows_for_day for why.
                continue
            rows.append(
                {"ticker": ticker, "close": float(close_value), "quant_score": float(qs), "news_score": None}
            )
        if rows:
            signals_df = pd.DataFrame(rows)
            for model in registry.all():
                daily_script.process_model_for_day(model, score_portfolios[model.name], signals_df, date_str)

        # -- price-criteria models (price_model_1 / price_model_2 / whatever's configured) --
        if pc_models:
            criteria_rows_by_model = _generate_criteria_rows_for_day(ticker_data, date, price_criteria_params)
            for model_name, rows_for_model in criteria_rows_by_model.items():
                if not rows_for_model:
                    continue
                market_context_by_ticker = {
                    row["ticker"]: _market_context_for_day(row["ticker"], date, ticker_data, regime_by_date)
                    for row in rows_for_model
                }
                _apply_price_criteria_day(
                    pc_models[model_name], pc_portfolios[model_name], rows_for_model, date_str, market_context_by_ticker
                )

    all_portfolios = {**score_portfolios, **pc_portfolios}
    models_output: dict[str, dict] = {}
    for name, portfolio in all_portfolios.items():
        equity_df = portfolio.equity_df()
        periods = bucket_equity_by_period(equity_df, freq=period_freq)
        models_output[name] = {
            "starting_cash": portfolio.starting_cash,
            "final_equity": float(equity_df["equity"].iloc[-1]) if not equity_df.empty else portfolio.starting_cash,
            "total_return": total_return(equity_df),
            "n_trades": len(portfolio.trades),
            "n_trading_days": len(equity_df),
            "periods": [p.to_dict() for p in periods],
        }

    # -- benchmark buy-and-hold references (SPY / QQQ / TLT) --
    # QQQ was already fetched above for the market-regime filter -- reused
    # here instead of fetched a second time. SPY/TLT are fetched fresh.
    benchmarks_output: dict[str, dict] = {}
    for ticker, label in BENCHMARK_TICKERS.items():
        try:
            if ticker == MARKET_INDEX_TICKER and not qqq_ohlcv.empty:
                bench_ohlcv = qqq_ohlcv
            else:
                bench_ohlcv = fetch_ohlcv(ticker, period=FETCH_PERIOD)
            benchmarks_output[ticker] = _buy_and_hold_payload(bench_ohlcv, label, period_freq)
        except Exception as exc:  # noqa: BLE001 -- one bad benchmark ticker must not crash the whole run
            failures.append((ticker, str(exc)))

    return {
        "run_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": _config(period_freq),
        "models": models_output,
        "benchmarks": benchmarks_output,
        "failures": failures,
    }


def _sanitize_nan(value):
    """Recursively replaces float('nan') with None so the output JSON is
    always valid (allow_nan=False below is what enforces that at write
    time). Last-resort safety net -- the real fix is not producing NaN in
    the first place (see the Close-value guards in run_backtest() and
    _generate_criteria_rows_for_day() above, added 2026-09-22 after a NaN
    yfinance Close crashed this script the first time price_model_1/2
    actually started trading), but this keeps one bad number from taking
    down the whole run if some other path produces one."""
    if isinstance(value, float) and value != value:  # NaN != NaN is the cheapest isnan check
        return None
    if isinstance(value, dict):
        return {k: _sanitize_nan(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_nan(v) for v in value]
    return value


def main() -> int:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = _sanitize_nan(run_backtest())
    OUTPUT_PATH.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8"
    )

    n_models = len(result["models"])
    n_benchmarks = len(result.get("benchmarks", {}))
    print(f"[backtest] {n_models} models + {n_benchmarks} benchmarks backtested over {FETCH_PERIOD} history ({PERIOD_FREQ} periods)")
    for name, payload in result["models"].items():
        tr = payload["total_return"]
        tr_str = f"{tr:.1%}" if tr is not None else "n/a"
        print(f"  {name}: total_return={tr_str}, final_equity={payload['final_equity']:.2f}, trades={payload['n_trades']}")
    for name, payload in result.get("benchmarks", {}).items():
        tr = payload["total_return"]
        tr_str = f"{tr:.1%}" if tr is not None else "n/a"
        print(f"  [benchmark] {name}: total_return={tr_str}, final_equity={payload['final_equity']:.2f}")

    if result["failures"]:
        for ticker, err in result["failures"]:
            print(f"  [FAIL] {ticker}: {err}")
        if not result["models"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

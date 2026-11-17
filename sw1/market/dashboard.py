"""
SW1 -- assembles the Fin-tip_lab market dashboard data.

All network access goes through a `MarketSource` (scripts/build_market_dashboard.py
supplies the yfinance/FRED one; tests supply a fake), so everything in this
module is deterministic and testable offline.

Outputs (plain dicts, ready for json.dump):
  * one detail dict per ticker   -> data/market/tickers/{TICKER}.json
  * a watchlist summary          -> data/market/watchlist.json
  * a macro block                -> data/market/macro.json

A ticker that fails to load is listed under `failures` instead of
stopping the run, and every optional block (options, rates, ...) degrades
to None on its own. When today's option snapshot fails the quality check
(Yahoo off-hours data with open interest zeroed out), the last good
snapshot from the previous run is carried forward for up to
MAX_STALE_OPTION_DAYS and marked `options_stale`.
"""
from __future__ import annotations

import math
from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Protocol

import numpy as np
import pandas as pd

from sw1.indicators.io import to_market_dates
from sw1.market.options import ExpiryChain, analyze_chains, chain_quality
from sw1.market.signal_stats import evaluate_all, headline_signal
from sw1.market.trend import compute_trend_position, compute_trend_strength
from sw1.market.universe import INDEX_GROUP, Member, members

MAX_OPTION_EXPIRIES = 6
MAX_OPTION_DAYS = 45
MAX_STALE_OPTION_DAYS = 7


class OptionsDataError(RuntimeError):
    """Option chains came back but the data is unusable (e.g. OI all zero)."""


class MarketSource(Protocol):
    def history(self, ticker: str, period: str) -> pd.DataFrame: ...
    def option_expiries(self, ticker: str) -> list[str]: ...
    def option_chain(self, ticker: str, expiry: str) -> tuple[pd.DataFrame, pd.DataFrame]: ...
    def fred(self, series_id: str) -> pd.Series: ...


# ---------------------------------------------------------------- helpers

def clean_json(obj):
    """Recursively turns NaN/inf into None and numpy scalars into Python
    ones so json.dump(allow_nan=False) never fails."""
    if isinstance(obj, dict):
        return {k: clean_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean_json(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return None if (math.isnan(f) or math.isinf(f)) else f
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def normalize_history(df: pd.DataFrame) -> pd.DataFrame:
    """Daily OHLCV with a tz-naive, midnight, de-duplicated market-date index."""
    out = df[["Open", "High", "Low", "Close", "Volume"]].copy()
    out.index = to_market_dates(out.index).normalize()
    out = out[~out.index.duplicated(keep="last")].sort_index()
    return out.dropna(subset=["Close"])


def _pct(a, b):
    if a is None or b is None or b == 0 or pd.isna(a) or pd.isna(b):
        return None
    return round((float(a) / float(b) - 1) * 100, 2)


def _percentile(series: pd.Series, value: float) -> float | None:
    s = series.dropna()
    if len(s) < 20 or value is None or pd.isna(value):
        return None
    return round(float((s <= value).mean()), 3)


def _last_two(series: pd.Series) -> tuple[float | None, float | None]:
    s = series.dropna()
    if len(s) == 0:
        return None, None
    last = float(s.iloc[-1])
    prev = float(s.iloc[-2]) if len(s) > 1 else None
    return last, prev


# ---------------------------------------------------------------- per ticker

def build_options(source: MarketSource, ticker: str, spot: float, as_of: date) -> dict | None:
    expiries = []
    for e in source.option_expiries(ticker):
        try:
            d = date.fromisoformat(e)
        except ValueError:
            continue
        if as_of < d <= date.fromordinal(as_of.toordinal() + MAX_OPTION_DAYS):
            expiries.append(d)
    expiries = sorted(expiries)[:MAX_OPTION_EXPIRIES]
    chains = []
    for d in expiries:
        calls, puts = source.option_chain(ticker, d.isoformat())
        chains.append(ExpiryChain(expiry=d, calls=calls, puts=puts))
    if not chains:
        return None
    ok, reason = chain_quality(chains, spot)
    if not ok:
        raise OptionsDataError(reason)
    return dict(asdict(analyze_chains(chains, spot=spot, as_of=as_of)), quality_ok=True)


def build_ticker(source: MarketSource, member: Member, with_options: bool = True) -> dict:
    hist = normalize_history(source.history(member.ticker, "10y"))
    if len(hist) < 30:
        raise ValueError(f"only {len(hist)} daily bars")
    close = hist["Close"]
    as_of = hist.index[-1].date()
    last = float(close.iloc[-1])

    trend = compute_trend_strength(close)
    position = compute_trend_position(close)
    stats = evaluate_all(hist)
    head = headline_signal(stats)

    vol = hist["Volume"].astype(float)
    vol_avg20 = vol.iloc[-21:-1].mean() if len(vol) > 21 else None
    volume_ratio = round(float(vol.iloc[-1] / vol_avg20), 2) if vol_avg20 else None

    options = None
    options_error = None
    if with_options:
        try:
            options = build_options(source, member.ticker, last, as_of)
        except Exception as exc:  # noqa: BLE001 -- options are optional per ticker
            options_error = str(exc)[:200]

    return {
        "ticker": member.ticker,
        "name": member.name,
        "group": member.group,
        "as_of": as_of.isoformat(),
        "close": round(last, 4),
        "change_pct": _pct(last, close.iloc[-2]) if len(close) > 1 else None,
        "volume_ratio_20d": volume_ratio,
        "trend": asdict(trend),
        "position": asdict(position),
        "signals": [asdict(s) for s in stats],
        "headline_signal": asdict(head) if head else None,
        "options": options,
        "options_error": options_error,
        "options_stale": False,
        "history_bars": len(hist),
        "chart": [
            {"d": d.date().isoformat(), "c": round(float(c), 4)}
            for d, c in close.iloc[-260:].items()
        ],
    }


def summary_row(detail: dict) -> dict:
    """The slice of a ticker's detail the watchlist table needs."""
    opt = detail.get("options") or {}
    head = detail.get("headline_signal") or {}
    return {
        "ticker": detail["ticker"],
        "name": detail["name"],
        "group": detail["group"],
        "as_of": detail["as_of"],
        "close": detail["close"],
        "change_pct": detail["change_pct"],
        "volume_ratio_20d": detail["volume_ratio_20d"],
        "trend_score": detail["trend"]["score"],
        "trend_label": detail["trend"]["label"],
        "pct_from_high": detail["position"]["pct_from_high"],
        "return_20d": detail["position"]["return_20d"],
        "above_ma50": (detail["position"]["pct_vs_ma50"] or 0) > 0 if detail["position"]["pct_vs_ma50"] is not None else None,
        "signal": {
            "key": head.get("key"),
            "label": head.get("label"),
            "kind": head.get("kind"),
            "timeframe": head.get("timeframe"),
            "bars_ago": head.get("bars_ago"),
            "n": head.get("n"),
            "excess_pct": head.get("excess_pct"),
            "win_rate": head.get("win_rate"),
            "low_sample": head.get("low_sample"),
        } if head else None,
        "gamma_regime": opt.get("regime"),
        "expected_move_pct": opt.get("expected_move_pct"),
        "pc_ratio_oi": opt.get("pc_ratio_oi"),
        "options_stale": bool(detail.get("options_stale")),
        "options_as_of": opt.get("as_of"),
    }


def carry_forward_options(detail: dict, previous: dict | None) -> dict:
    """If today's options are missing because the data was bad, reuse the
    previous run's snapshot (marked stale) as long as it is recent."""
    if detail.get("options") is not None or not detail.get("options_error") or not previous:
        return detail
    prev_opt = previous.get("options")
    # Only snapshots that passed the quality check can be carried forward.
    if not prev_opt or not prev_opt.get("as_of") or not prev_opt.get("quality_ok"):
        return detail
    age = (date.fromisoformat(detail["as_of"]) - date.fromisoformat(prev_opt["as_of"])).days
    if 0 <= age <= MAX_STALE_OPTION_DAYS:
        detail = dict(detail, options=prev_opt, options_stale=True)
    return detail


# ---------------------------------------------------------------- macro

STRIP = (
    ("SPY", "S&P 500", "pct"),
    ("QQQ", "나스닥 100", "pct"),
    ("^TNX", "미 10년물 금리", "bp"),
    ("DX-Y.NYB", "달러인덱스", "pct"),
    ("CL=F", "WTI 원유", "pct"),
    ("GC=F", "금", "pct"),
    ("BTC-USD", "비트코인", "pct"),
    ("^VIX", "VIX", "pct"),
)

VOL_TICKERS = ("^VIX", "^VIX3M", "^VIX9D", "^VVIX", "^VXN")


def _close_series(source: MarketSource, ticker: str, period: str = "2y") -> pd.Series | None:
    try:
        return normalize_history(source.history(ticker, period))["Close"]
    except Exception:  # noqa: BLE001
        return None


def build_strip(source: MarketSource) -> list[dict]:
    out = []
    for ticker, label, unit in STRIP:
        s = _close_series(source, ticker)
        if s is None or len(s) == 0:
            out.append({"ticker": ticker, "label": label, "last": None, "change": None, "unit": unit})
            continue
        last, prev = _last_two(s)
        if unit == "bp":
            change = round((last - prev) * 100, 1) if prev is not None else None
        else:
            change = _pct(last, prev)
        out.append({"ticker": ticker, "label": label, "last": round(last, 4), "change": change, "unit": unit,
                    "as_of": s.index[-1].date().isoformat()})
    return out


def _term_label(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    return "contango" if ratio < 0.95 else "backwardation" if ratio > 1.05 else "flat"


def build_volatility(source: MarketSource, qqq: pd.Series | None) -> dict:
    series = {t: _close_series(source, t) for t in VOL_TICKERS}
    last = {t: (_last_two(s)[0] if s is not None else None) for t, s in series.items()}
    ratio = round(last["^VIX"] / last["^VIX3M"], 3) if last["^VIX"] and last["^VIX3M"] else None
    realized = None
    if qqq is not None and len(qqq) > 21:
        rets = np.log(qqq / qqq.shift(1)).dropna().iloc[-20:]
        realized = round(float(rets.std() * math.sqrt(252) * 100), 2)
    vxn = last["^VXN"]
    vxn_series = series["^VXN"]
    return {
        "vix": last["^VIX"],
        "vix3m": last["^VIX3M"],
        "vix9d": last["^VIX9D"],
        "vvix": last["^VVIX"],
        "vxn": vxn,
        "vxn_pctile_1y": _percentile(vxn_series.iloc[-252:], vxn) if vxn_series is not None else None,
        "term_ratio": ratio,
        "term_label": _term_label(ratio),
        "qqq_realized_vol_20d": realized,
        "vol_premium": round(vxn - realized, 2) if (vxn is not None and realized is not None) else None,
    }


def build_rates(source: MarketSource, qqq: pd.Series | None) -> dict | None:
    try:
        tips = source.fred("DFII10").dropna()
        be = source.fred("T10YIE").dropna()
    except Exception:  # noqa: BLE001 -- FRED is optional
        return None
    if len(tips) == 0:
        return None
    tips_last = float(tips.iloc[-1])
    chg20 = round((tips_last - float(tips.iloc[-21])) * 100, 1) if len(tips) > 21 else None
    corr = None
    if qqq is not None:
        joined = pd.concat([qqq.pct_change(), tips.diff()], axis=1, join="inner").dropna().iloc[-60:]
        if len(joined) >= 30:
            corr = round(float(joined.iloc[:, 0].corr(joined.iloc[:, 1])), 2)
    return {
        "tips10": round(tips_last, 3),
        "tips10_as_of": tips.index[-1].date().isoformat(),
        "tips10_change_20d_bp": chg20,
        "tips10_pctile_1y": _percentile(tips.iloc[-252:], tips_last),
        "breakeven10": round(float(be.iloc[-1]), 3) if len(be) else None,
        "corr_qqq_vs_real_rate_60d": corr,
    }


def _gap(a: pd.Series, b: pd.Series, window: int = 20) -> dict | None:
    joined = pd.concat([a, b], axis=1, join="inner").dropna()
    if len(joined) < window + 60:
        return None
    ra = joined.iloc[:, 0].pct_change(window) * 100
    rb = joined.iloc[:, 1].pct_change(window) * 100
    gap = (ra - rb).dropna()
    hist = gap.iloc[-252:]
    std = float(hist.std())
    latest = float(gap.iloc[-1])
    return {
        "gap_20d_pct": round(latest, 2),
        "z": round((latest - float(hist.mean())) / std, 2) if std > 0 else None,
    }


def build_indices(source: MarketSource) -> dict:
    s = {t: _close_series(source, t) for t in ("QQQ", "SPY", "DIA", "QQQE")}
    curves = {}
    for t in ("QQQ", "SPY", "DIA"):
        if s[t] is not None and len(s[t]) > 0:
            tail = s[t].iloc[-126:]
            curves[t] = [{"d": d.date().isoformat(), "r": round((float(v) / float(tail.iloc[0]) - 1) * 100, 2)} for d, v in tail.items()]
    out = {"curves_6m": curves, "qqq_vs_spy": None, "qqq_vs_dia": None, "concentration": None}
    if s["QQQ"] is not None and s["SPY"] is not None:
        out["qqq_vs_spy"] = _gap(s["QQQ"], s["SPY"])
    if s["QQQ"] is not None and s["DIA"] is not None:
        out["qqq_vs_dia"] = _gap(s["QQQ"], s["DIA"])
    if s["QQQ"] is not None and s["QQQE"] is not None:
        q, e = s["QQQ"], s["QQQE"]
        out["concentration"] = {
            "qqq_20d": _pct(q.iloc[-1], q.iloc[-21]) if len(q) > 21 else None,
            "qqqe_20d": _pct(e.iloc[-1], e.iloc[-21]) if len(e) > 21 else None,
        }
        c = out["concentration"]
        c["diff_20d"] = round(c["qqq_20d"] - c["qqqe_20d"], 2) if (c["qqq_20d"] is not None and c["qqqe_20d"] is not None) else None
    return out


def build_breadth(rows: list[dict]) -> dict:
    """Breadth over the stock rows only (index ETFs left out)."""
    stocks = [r for r in rows if r["group"] != INDEX_GROUP]
    known = [r for r in stocks if r["above_ma50"] is not None]
    return {
        "n": len(stocks),
        "pct_above_ma50": round(sum(r["above_ma50"] for r in known) / len(known), 3) if known else None,
        "within_5pct_of_high": sum(1 for r in stocks if r["pct_from_high"] is not None and r["pct_from_high"] >= -5),
        "median_return_20d": round(float(np.median([r["return_20d"] for r in stocks if r["return_20d"] is not None])), 2)
        if any(r["return_20d"] is not None for r in stocks) else None,
    }


# ---------------------------------------------------------------- everything

def build_all(source: MarketSource, universe: list[Member] | None = None, with_options: bool = True, log=print,
              previous: dict | None = None):
    """`previous`: ticker -> detail dict from the last run (for carrying
    forward option snapshots when today's are unusable)."""
    universe = universe or members()
    previous = previous or {}
    details, failures = {}, []
    for m in universe:
        try:
            detail = build_ticker(source, m, with_options=with_options)
            details[m.ticker] = carry_forward_options(detail, previous.get(m.ticker))
            if detail.get("options_error"):
                log(f"[WARN] {m.ticker} options: {detail['options_error']}" + (" (kept previous snapshot)" if details[m.ticker]["options_stale"] else ""))
        except Exception as exc:  # noqa: BLE001 -- one bad ticker must not stop the run
            failures.append({"ticker": m.ticker, "error": str(exc)[:200]})
            log(f"[WARN] {m.ticker}: {exc}")

    rows = [summary_row(details[m.ticker]) for m in universe if m.ticker in details]
    qqq = _close_series(source, "QQQ")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    as_of = max((r["as_of"] for r in rows), default=None)

    macro = {
        "generated_at": generated_at,
        "as_of": as_of,
        "strip": build_strip(source),
        "volatility": build_volatility(source, qqq),
        "rates": build_rates(source, qqq),
        "indices": build_indices(source),
        "breadth": build_breadth(rows),
    }
    watchlist = {
        "generated_at": generated_at,
        "as_of": as_of,
        "groups": list(dict.fromkeys(m.group for m in universe)),
        "rows": rows,
        "failures": failures,
    }
    return clean_json(watchlist), {t: clean_json(d) for t, d in details.items()}, clean_json(macro)

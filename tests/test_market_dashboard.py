from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sw1.market.dashboard import build_all, clean_json, normalize_history
from sw1.market.universe import Member, members

from .conftest import make_mock_ohlcv

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_market_dashboard as script  # noqa: E402


def yahoo_like(n=600, seed=1, start_price=150.0):
    """OHLCV shaped like yfinance's: tz-aware New York index."""
    df = make_mock_ohlcv(n=n, seed=seed, start_price=start_price)
    df.index = df.index.tz_localize("America/New_York")
    return df


def chain_side(spot, oi):
    strikes = np.arange(round(spot * 0.8), round(spot * 1.2), max(1, round(spot / 40)))
    return pd.DataFrame({"strike": strikes, "openInterest": oi, "volume": 10, "impliedVolatility": 0.35})


class FakeSource:
    def __init__(self, broken=(), no_options=(), broken_chain=(), fred_fails=False):
        self.broken, self.no_options, self.broken_chain, self.fred_fails = set(broken), set(no_options), set(broken_chain), fred_fails
        self._hist = {}

    def history(self, ticker, period):
        if ticker in self.broken:
            raise RuntimeError(f"{ticker}: no price history")
        if ticker not in self._hist:
            self._hist[ticker] = yahoo_like(seed=abs(hash(ticker)) % 1000)
        return self._hist[ticker]

    def _last_date(self, ticker):
        return self.history(ticker, "10y").index[-1].date()

    def option_expiries(self, ticker):
        if ticker in self.no_options:
            return []
        last = self._last_date(ticker)
        return [(last + timedelta(days=d)).isoformat() for d in (-1, 3, 10, 30, 90)]  # one expired, one too far

    def option_chain(self, ticker, expiry):
        if ticker in self.broken_chain:
            raise RuntimeError("chain unavailable")
        spot = float(self.history(ticker, "10y")["Close"].iloc[-1])
        return chain_side(spot, 500), chain_side(spot, 700)

    def fred(self, series_id):
        if self.fred_fails:
            raise RuntimeError("FRED down")
        idx = pd.bdate_range(end="2026-11-13", periods=400)
        base = 2.0 if series_id == "DFII10" else 2.3
        return pd.Series(base + np.linspace(0, 0.5, len(idx)), index=idx)


UNIVERSE = [Member("AAA", "Alpha", "빅테크"), Member("BBB", "Beta", "반도체"), Member("SPY", "S&P 500 ETF", "지수·ETF")]


def test_normalize_history_strips_timezone_and_keeps_dates():
    df = yahoo_like(n=50)
    out = normalize_history(df)
    assert out.index.tz is None
    assert list(out.index.date) == list(df.index.date)


def test_build_all_produces_rows_details_and_macro():
    watch, details, macro = build_all(FakeSource(), UNIVERSE, log=lambda *_: None)
    assert [r["ticker"] for r in watch["rows"]] == ["AAA", "BBB", "SPY"]
    assert watch["failures"] == []
    d = details["AAA"]
    assert -15 <= d["trend"]["score"] <= 15
    assert len(d["signals"]) == 12
    assert d["options"] is not None
    # only the 3/10/30-day expiries are used: expired and >45-day ones are dropped
    assert [e["days"] for e in d["options"]["by_expiry"]] == [3, 10, 30]
    assert len(d["chart"]) == 260
    assert macro["breadth"]["n"] == 2  # SPY is not a stock
    assert macro["rates"]["tips10"] == pytest.approx(2.5)
    assert {s["ticker"] for s in macro["strip"]} >= {"SPY", "^TNX", "BTC-USD"}


def test_one_broken_ticker_is_listed_not_fatal():
    watch, details, _ = build_all(FakeSource(broken={"BBB"}), UNIVERSE, log=lambda *_: None)
    assert [f["ticker"] for f in watch["failures"]] == ["BBB"]
    assert "BBB" not in details and len(watch["rows"]) == 2


def test_missing_or_broken_options_degrade_per_ticker():
    _, details, _ = build_all(FakeSource(no_options={"AAA"}, broken_chain={"BBB"}), UNIVERSE, log=lambda *_: None)
    assert details["AAA"]["options"] is None and details["AAA"]["options_error"] is None
    assert details["BBB"]["options"] is None and "chain unavailable" in details["BBB"]["options_error"]


def test_fred_outage_leaves_rates_empty():
    _, _, macro = build_all(FakeSource(fred_fails=True), UNIVERSE, log=lambda *_: None)
    assert macro["rates"] is None
    assert macro["volatility"] is not None


def test_everything_is_strict_json():
    watch, details, macro = build_all(FakeSource(), UNIVERSE, log=lambda *_: None)
    for obj in (watch, macro, *details.values()):
        json.dumps(obj, allow_nan=False)


def test_clean_json_replaces_nan_and_numpy_types():
    out = clean_json({"a": float("nan"), "b": np.float64(1.5), "c": [np.int64(2), np.bool_(True)], "d": float("inf")})
    assert out == {"a": None, "b": 1.5, "c": [2, True], "d": None}


def test_script_writes_all_files(tmp_path):
    src = FakeSource()
    real_members = script.build_all.__globals__["members"]
    script.build_all.__globals__["members"] = lambda: UNIVERSE
    try:
        assert script.main(source=src, out_dir=tmp_path) == 0
    finally:
        script.build_all.__globals__["members"] = real_members
    assert (tmp_path / "watchlist.json").exists() and (tmp_path / "macro.json").exists()
    assert sorted(p.name for p in (tmp_path / "tickers").iterdir()) == ["AAA.json", "BBB.json", "SPY.json"]


def test_script_returns_1_when_nothing_loads(tmp_path):
    broken = FakeSource(broken={m.ticker for m in members()} | {"AAA", "BBB", "SPY"})
    real_members = script.build_all.__globals__["members"]
    script.build_all.__globals__["members"] = lambda: UNIVERSE
    try:
        assert script.main(source=broken, out_dir=tmp_path) == 1
    finally:
        script.build_all.__globals__["members"] = real_members
    assert not (tmp_path / "watchlist.json").exists()


def test_universe_has_no_duplicate_tickers():
    tickers = [m.ticker for m in members()]
    assert len(tickers) == len(set(tickers))


class ZeroOISource(FakeSource):
    """Chains that look like Yahoo's off-hours data: OI all zero."""

    def option_chain(self, ticker, expiry):
        spot = float(self.history(ticker, "10y")["Close"].iloc[-1])
        return chain_side(spot, 0), chain_side(spot, 0)


def _previous(options_as_of, quality_ok=True):
    return {"AAA": {"options": {"as_of": options_as_of, "regime": "positive", "expected_move_pct": 1.2, "pc_ratio_oi": 0.9,
                                **({"quality_ok": True} if quality_ok else {})}}}


def test_zeroed_open_interest_is_rejected_not_published():
    _, details, _ = build_all(ZeroOISource(), UNIVERSE, log=lambda *_: None)
    assert details["AAA"]["options"] is None
    assert "open interest" in details["AAA"]["options_error"]


def test_good_snapshot_carries_forward_and_is_marked_stale():
    src = ZeroOISource()
    as_of = src.history("AAA", "10y").index[-1].date()
    watch, details, _ = build_all(src, UNIVERSE, log=lambda *_: None, previous=_previous((as_of - timedelta(days=1)).isoformat()))
    assert details["AAA"]["options_stale"] is True
    assert details["AAA"]["options"]["regime"] == "positive"
    row = [r for r in watch["rows"] if r["ticker"] == "AAA"][0]
    assert row["options_stale"] is True and row["gamma_regime"] == "positive"


def test_unchecked_or_old_snapshots_are_not_carried_forward():
    src = ZeroOISource()
    as_of = src.history("AAA", "10y").index[-1].date()
    _, d1, _ = build_all(src, UNIVERSE, log=lambda *_: None, previous=_previous((as_of - timedelta(days=1)).isoformat(), quality_ok=False))
    _, d2, _ = build_all(src, UNIVERSE, log=lambda *_: None, previous=_previous((as_of - timedelta(days=30)).isoformat()))
    assert d1["AAA"]["options"] is None and d2["AAA"]["options"] is None


def test_fresh_snapshots_are_marked_quality_ok():
    _, details, _ = build_all(FakeSource(), UNIVERSE, log=lambda *_: None)
    assert details["AAA"]["options"]["quality_ok"] is True and details["AAA"]["options_stale"] is False


class RichSource(FakeSource):
    """Also has the optional news / analyst methods."""

    def news(self, ticker):
        when = int(pd.Timestamp.now(tz="UTC").timestamp()) - 3600
        return [{"title": f"{ticker} beats estimates", "publisher": "P", "link": f"https://y/{ticker}", "providerPublishTime": when}]

    def upgrades(self, ticker):
        if ticker == "BBB":
            raise RuntimeError("no analyst data")
        day = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=3)
        return pd.DataFrame({"Firm": ["F"], "ToGrade": ["Sell"], "FromGrade": ["Hold"], "Action": ["down"],
                             "priceTargetAction": ["Lowers"], "currentPriceTarget": [90.0], "priorPriceTarget": [120.0]},
                            index=pd.DatetimeIndex([day], name="GradeDate"))

    def price_targets(self, ticker):
        return {"mean": 200.0, "high": 250.0, "low": 150.0, "median": 195.0}

    def next_earnings(self, ticker):
        from datetime import date
        return date(2026, 11, 19) if ticker == "AAA" else None


def test_news_targets_and_macro_reach_the_rows():
    watch, details, _ = build_all(RichSource(), UNIVERSE, log=lambda *_: None)
    row = {r["ticker"]: r for r in watch["rows"]}
    assert row["AAA"]["news_tone"] == "positive" and row["AAA"]["target_tone"] == "negative"
    assert row["BBB"]["target_tone"] is None  # its analyst call failed; the row still builds
    assert row["AAA"]["macro_fit"] in ("favorable", "neutral", "unfavorable")
    assert row["AAA"]["next_earnings"] == "2026-11-19" and row["BBB"]["next_earnings"] is None
    d = details["AAA"]
    assert d["news"][0]["link"] == "https://y/AAA" and d["analyst"]["consensus"]["mean"] == 200.0
    assert {f["key"] for f in d["macro"]["factors"]} == {"rates", "dollar", "oil"}
    json.dumps(d, allow_nan=False)


def test_plain_source_leaves_the_new_columns_empty():
    watch, _, _ = build_all(FakeSource(), UNIVERSE, log=lambda *_: None)
    r = watch["rows"][0]
    assert r["news_tone"] is None and r["target_tone"] is None


def test_flow_block_and_yesterdays_move_check():
    src = FakeSource()
    as_of = src.history("AAA", "10y").index[-1].date()
    prev = {"AAA": {"options": {"as_of": (as_of - timedelta(days=1)).isoformat(), "expected_move_pct": 50.0}}}
    watch, details, _ = build_all(src, UNIVERSE, log=lambda *_: None, previous=prev)
    f = details["AAA"]["flow"]
    assert f["volatility"]["realized_20d"] > 0 and "seasonality" in f  # fake history is too short for 3 past Novembers
    assert f["move_check"]["expected_pct"] == 50.0 and f["move_check"]["within"] is True
    assert details["BBB"]["flow"]["move_check"] is None
    assert watch["rows"][0]["return_5d"] is not None


def test_macro_has_the_fear_greed_index():
    _, _, macro = build_all(FakeSource(), UNIVERSE, log=lambda *_: None)
    fg = macro["fear_greed"]
    assert 0 <= fg["score"] <= 100 and fg["label"] and len(fg["components"]) >= 5
    json.dumps(macro, allow_nan=False)


def test_ticker_news_gets_korean_titles():
    from sw1.market.translate import Translator

    tr = Translator(backend=lambda texts: [f"한글 {t}" for t in texts])
    _, details, _ = build_all(RichSource(), UNIVERSE, log=lambda *_: None, translator=tr)
    news = details["AAA"]["news"]
    assert news and all(n["title_ko"].startswith("한글 ") for n in news)

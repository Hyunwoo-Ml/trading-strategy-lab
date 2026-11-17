"""
Builds the Fin-tip_lab market dashboard data (data/market/) from Yahoo
Finance (prices, option chains) and FRED (real rates).

Runs on GitHub Actions (.github/workflows/market-dashboard.yml). Yahoo is
not reachable from the Cowork sandbox, so locally this is exercised only
through tests/test_market_dashboard.py with a fake source.

Writes:
  data/market/watchlist.json        one summary row per ticker
  data/market/macro.json            market strip, volatility, rates, indices, breadth
  data/market/tickers/{TICKER}.json full detail per ticker (signals, options, chart)
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.market.dashboard import build_all  # noqa: E402

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_DIR = PROJECT_ROOT / "data" / "market"
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"


class YahooFredSource:
    """The real MarketSource. Small pauses between option-chain calls keep
    the run polite to Yahoo."""

    def __init__(self, pause_seconds: float = 0.2):
        import yfinance as yf

        self._yf = yf
        self._tickers = {}
        self.pause = pause_seconds

    def _t(self, ticker: str):
        if ticker not in self._tickers:
            self._tickers[ticker] = self._yf.Ticker(ticker)
        return self._tickers[ticker]

    def history(self, ticker: str, period: str) -> pd.DataFrame:
        hist = self._t(ticker).history(period=period, auto_adjust=True)
        if hist is None or hist.empty:
            raise RuntimeError(f"{ticker}: no price history")
        return hist

    def option_expiries(self, ticker: str) -> list[str]:
        try:
            return list(self._t(ticker).options)
        except Exception:  # noqa: BLE001 -- no listed options
            return []

    def option_chain(self, ticker: str, expiry: str):
        time.sleep(self.pause)
        chain = self._t(ticker).option_chain(expiry)
        return chain.calls, chain.puts

    def fred(self, series_id: str) -> pd.Series:
        resp = requests.get(FRED_CSV.format(series=series_id), timeout=30)
        resp.raise_for_status()
        df = pd.read_csv(io.StringIO(resp.text))
        date_col, value_col = df.columns[0], df.columns[1]
        s = pd.to_numeric(df[value_col], errors="coerce")
        s.index = pd.to_datetime(df[date_col])
        return s.dropna()


def write_outputs(watchlist: dict, details: dict, macro: dict, out_dir: Path = MARKET_DIR) -> None:
    tickers_dir = out_dir / "tickers"
    tickers_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "watchlist.json").write_text(json.dumps(watchlist, ensure_ascii=False, allow_nan=False, indent=1), encoding="utf-8")
    (out_dir / "macro.json").write_text(json.dumps(macro, ensure_ascii=False, allow_nan=False, indent=1), encoding="utf-8")
    for ticker, detail in details.items():
        (tickers_dir / f"{ticker}.json").write_text(
            json.dumps(detail, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8"
        )


def load_previous(out_dir: Path) -> dict:
    """Last run's per-ticker details, used to carry forward option snapshots."""
    previous = {}
    for path in (out_dir / "tickers").glob("*.json"):
        try:
            previous[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return previous


def main(source=None, out_dir: Path = MARKET_DIR) -> int:
    source = source or YahooFredSource()
    watchlist, details, macro = build_all(source, previous=load_previous(out_dir))
    if not watchlist["rows"]:
        print("[ERROR] no tickers loaded -- nothing written")
        return 1
    write_outputs(watchlist, details, macro, out_dir)
    print(f"wrote {len(details)} tickers, {len(watchlist['failures'])} failures, as of {watchlist['as_of']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

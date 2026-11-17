from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from sw1.market.live import build_live, futures_symbol, upcoming_meeting

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_market_live as script  # noqa: E402

NOW = datetime(2026, 11, 17, 16, 0, tzinfo=timezone.utc)  # 11am New York


class LiveSource:
    """Strip tickers trade around 100; fed funds futures price a 70% chance of a December cut."""

    def __init__(self, futures=None, fred_fails=False, quotes_fail=False):
        start, after = 3.88, 3.88 - 0.25 * 0.7
        dec_avg = (9 / 31) * start + (22 / 31) * after
        self.futures = futures if futures is not None else {"ZQX26.CBT": 100 - start, "ZQZ26.CBT": 100 - dec_avg, "ZQF27.CBT": 96.5}
        self.fred_fails, self.quotes_fail = fred_fails, quotes_fail

    def history(self, ticker, period):
        idx = pd.DatetimeIndex(["2026-11-16", "2026-11-17"]).tz_localize("America/New_York")
        if ticker.startswith("ZQ"):
            if ticker not in self.futures:
                raise RuntimeError("no such contract")
            p = self.futures[ticker]
            return pd.DataFrame({"Open": p, "High": p, "Low": p, "Close": [p, p], "Volume": 0}, index=idx)
        if self.quotes_fail:
            raise RuntimeError("Yahoo down")
        return pd.DataFrame({"Open": 100, "High": 101, "Low": 99, "Close": [100.0, 101.0], "Volume": 1}, index=idx)

    def fred(self, series_id):
        if self.fred_fails:
            raise RuntimeError("FRED down")
        return pd.Series([3.88 if series_id == "EFFR" else 4.0], index=pd.to_datetime(["2026-11-16"]))


def test_symbols_and_meeting_rollover_after_the_2pm_statement():
    assert futures_symbol(2026, 12) == "ZQZ26.CBT"
    assert upcoming_meeting(NOW).isoformat() == "2026-12-09"
    dec9_morning = datetime(2026, 12, 9, 15, 0, tzinfo=timezone.utc)  # 10am ET
    dec9_evening = datetime(2026, 12, 9, 20, 0, tzinfo=timezone.utc)  # 3pm ET
    assert upcoming_meeting(dec9_morning).isoformat() == "2026-12-09"
    assert upcoming_meeting(dec9_evening).isoformat() == "2027-01-27"


def test_build_live_has_strip_and_fed_odds():
    live = build_live(LiveSource(), NOW)
    assert live["generated_at"] == "2026-11-17T16:00:00Z"
    assert all(s["last"] == 101.0 for s in live["strip"])
    fw = live["fedwatch"]
    assert fw["meeting"] == "2026-12-09" and fw["cut"] == pytest.approx(0.7, abs=1e-3)
    assert fw["current_range"] == "3.75–4.00%" and "ZQZ26.CBT" in fw["futures"]


def test_missing_contracts_or_fred_degrade_to_an_error_not_a_crash():
    assert "error" in build_live(LiveSource(futures={}), NOW)["fedwatch"]
    fw = build_live(LiveSource(fred_fails=True), NOW)["fedwatch"]
    assert fw["cut"] == pytest.approx(0.7, abs=1e-3) and fw["current_range"] is None  # Nov contract still gives the start rate


def test_script_writes_json_and_refuses_when_no_quotes(tmp_path):
    out = tmp_path / "live.json"
    assert script.main(source=LiveSource(), out_path=out) == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["fedwatch"]["meeting"] == "2026-12-09"
    out2 = tmp_path / "none.json"
    assert script.main(source=LiveSource(quotes_fail=True), out_path=out2) == 1
    assert not out2.exists()

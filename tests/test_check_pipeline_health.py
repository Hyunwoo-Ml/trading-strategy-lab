"""Tests for scripts/check_pipeline_health.py.

2026-09-25: new script -- see its module docstring for why it exists
(active GitHub-issue alerting for a silently stalled pipeline, mirroring
docs/index.html's and docs/sw2.html's passive `pipelineDaysStale()` status
dot). Nothing here touches real repo data; every freshness check is
pointed at tmp_path fixtures."""
import csv
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_pipeline_health as script  # noqa: E402


def _now(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class TestDaysStaleUtc:
    def test_same_day_is_zero(self):
        assert script.days_stale_utc(date(2026, 9, 25), now=_now("2026-09-25T23:30:00")) == 0

    def test_counts_whole_calendar_days_ignoring_time_of_day(self):
        # last_date is a Monday; "now" is very early Friday morning UTC --
        # should still count 4 full days, not be thrown off by the clock.
        assert script.days_stale_utc(date(2026, 9, 21), now=_now("2026-09-25T00:01:00")) == 4

    def test_exactly_at_threshold_is_not_yet_flagged_by_build_report(self):
        # days_stale_utc itself has no notion of "stale" -- that's the
        # caller's `> threshold` comparison in build_report, tested below.
        assert script.days_stale_utc(date(2026, 9, 21), now=_now("2026-09-25T12:00:00")) == script.STALE_THRESHOLD_DAYS

    def test_one_day_past_threshold(self):
        assert (
            script.days_stale_utc(date(2026, 9, 20), now=_now("2026-09-25T12:00:00"))
            == script.STALE_THRESHOLD_DAYS + 1
        )


class TestCheckSw1Freshness:
    def test_missing_file_is_not_ok(self, tmp_path):
        result = script.check_sw1_freshness(csv_path=tmp_path / "missing.csv")
        assert result.ok is False
        assert result.last_date is None
        assert "not found" in result.error

    def test_empty_file_is_not_ok(self, tmp_path):
        path = tmp_path / "latest.csv"
        _write_csv(path, ["run_ts", "ticker"], [])
        result = script.check_sw1_freshness(csv_path=path)
        assert result.ok is False
        assert "empty" in result.error

    def test_unparseable_run_ts_is_not_ok(self, tmp_path):
        path = tmp_path / "latest.csv"
        _write_csv(path, ["run_ts", "ticker"], [{"run_ts": "not-a-date", "ticker": "AAPL"}])
        result = script.check_sw1_freshness(csv_path=path)
        assert result.ok is False
        assert "unparseable" in result.error

    def test_fresh_run_ts_computes_days_stale(self, tmp_path):
        path = tmp_path / "latest.csv"
        _write_csv(path, ["run_ts", "ticker"], [{"run_ts": "2026-09-24T22:06:21+00:00", "ticker": "AAPL"}])
        now = _now("2026-09-25T23:30:00")
        result = script.check_sw1_freshness(csv_path=path, now=now)
        assert result.ok is True
        assert result.last_date == date(2026, 9, 24)
        assert result.days_stale == 1

    def test_only_first_rows_run_ts_is_used(self, tmp_path):
        # main() writes one row per ticker, all sharing the same run_ts --
        # this only needs to read the first row, not scan every row.
        path = tmp_path / "latest.csv"
        _write_csv(
            path,
            ["run_ts", "ticker"],
            [
                {"run_ts": "2026-09-24T22:06:21+00:00", "ticker": "AAPL"},
                {"run_ts": "2026-09-24T22:06:21+00:00", "ticker": "MSFT"},
            ],
        )
        result = script.check_sw1_freshness(csv_path=path, now=_now("2026-09-24T23:00:00"))
        assert result.last_date == date(2026, 9, 24)


class TestCheckSw2Freshness:
    def test_no_files_at_all_is_not_ok(self, tmp_path):
        result = script.check_sw2_freshness(equity_dir=tmp_path / "equity", now=_now("2026-09-25T00:00:00"))
        assert result.ok is False
        assert "no readable" in result.error

    def test_files_exist_but_no_rows_yet_is_ok_with_no_date(self, tmp_path):
        equity_dir = tmp_path / "equity"
        _write_csv(equity_dir / "baseline.csv", ["date", "equity", "daily_return"], [])
        result = script.check_sw2_freshness(equity_dir=equity_dir, models=["baseline"], now=_now("2026-09-25T00:00:00"))
        assert result.ok is True
        assert result.last_date is None
        assert result.days_stale is None

    def test_missing_model_file_is_skipped_not_fatal(self, tmp_path):
        equity_dir = tmp_path / "equity"
        _write_csv(
            equity_dir / "baseline.csv",
            ["date", "equity", "daily_return"],
            [{"date": "2026-09-24", "equity": "100000", "daily_return": "0"}],
        )
        # "technical_only" has no file at all -- should not error out.
        result = script.check_sw2_freshness(
            equity_dir=equity_dir, models=["baseline", "technical_only"], now=_now("2026-09-25T12:00:00")
        )
        assert result.ok is True
        assert result.last_date == date(2026, 9, 24)
        assert result.days_stale == 1

    def test_takes_the_max_date_across_all_models(self, tmp_path):
        equity_dir = tmp_path / "equity"
        _write_csv(
            equity_dir / "baseline.csv",
            ["date", "equity", "daily_return"],
            [{"date": "2026-09-20", "equity": "100000", "daily_return": "0"}],
        )
        _write_csv(
            equity_dir / "technical_only.csv",
            ["date", "equity", "daily_return"],
            [{"date": "2026-09-24", "equity": "105000", "daily_return": "0.01"}],
        )
        result = script.check_sw2_freshness(
            equity_dir=equity_dir, models=["baseline", "technical_only"], now=_now("2026-09-25T12:00:00")
        )
        assert result.last_date == date(2026, 9, 24)
        assert result.days_stale == 1

    def test_readable_file_with_no_date_column_yields_no_date_but_still_ok(self, tmp_path):
        equity_dir = tmp_path / "equity"
        _write_csv(equity_dir / "baseline.csv", ["equity", "daily_return"], [{"equity": "100000", "daily_return": "0"}])
        result = script.check_sw2_freshness(
            equity_dir=equity_dir, models=["baseline"], now=_now("2026-09-25T12:00:00")
        )
        # A readable file whose rows just don't have a parseable "date"
        # (e.g. an unexpected schema, not a read failure) yields no usable
        # date -- same as an empty file. Confirms the "no readable files"
        # -> ok=False path only fires when nothing under equity_dir exists
        # or every file raises on read (both covered elsewhere).
        assert result.ok is True
        assert result.last_date is None


class TestBuildReport:
    def _fresh(self, d, days=0):
        return script.FreshnessResult(ok=True, last_date=d, days_stale=days)

    def _unreadable(self, error="boom"):
        return script.FreshnessResult(ok=False, last_date=None, days_stale=None, error=error)

    def test_both_fresh_is_not_stale(self):
        report = script.build_report(self._fresh(date(2026, 9, 24), 1), self._fresh(date(2026, 9, 24), 1))
        assert report.is_stale is False
        assert "정상 범위" in report.body
        assert report.title == script.ISSUE_TITLE

    def test_sw1_stale_only(self):
        report = script.build_report(
            self._fresh(date(2026, 9, 15), script.STALE_THRESHOLD_DAYS + 1),
            self._fresh(date(2026, 9, 24), 1),
        )
        assert report.is_stale is True
        assert "SW1" in report.body
        assert "SW2" not in report.body

    def test_sw2_stale_only(self):
        report = script.build_report(
            self._fresh(date(2026, 9, 24), 1),
            self._fresh(date(2026, 9, 15), script.STALE_THRESHOLD_DAYS + 1),
        )
        assert report.is_stale is True
        assert "SW2" in report.body
        assert "SW1" not in report.body

    def test_exactly_at_threshold_is_not_flagged(self):
        report = script.build_report(
            self._fresh(date(2026, 9, 21), script.STALE_THRESHOLD_DAYS),
            self._fresh(date(2026, 9, 24), 1),
        )
        assert report.is_stale is False

    def test_unreadable_sources_are_reported_even_without_days_stale(self):
        report = script.build_report(self._unreadable("latest.csv not found"), self._unreadable("no readable files"))
        assert report.is_stale is True
        assert "latest.csv not found" in report.body
        assert "no readable files" in report.body

    def test_title_is_a_stable_constant(self):
        r1 = script.build_report(self._fresh(date(2026, 9, 24)), self._fresh(date(2026, 9, 24)))
        r2 = script.build_report(self._unreadable(), self._unreadable())
        assert r1.title == r2.title == script.ISSUE_TITLE


class TestMain:
    def test_writes_body_file_and_github_output(self, tmp_path, monkeypatch):
        monkeypatch.setattr(script, "SIGNALS_CSV", tmp_path / "missing.csv")
        monkeypatch.setattr(script, "EQUITY_DIR", tmp_path / "missing_equity")
        body_path = tmp_path / "body.md"
        gh_output_path = tmp_path / "gh_output.txt"
        monkeypatch.setenv("PIPELINE_HEALTH_BODY_PATH", str(body_path))
        monkeypatch.setenv("GITHUB_OUTPUT", str(gh_output_path))

        rc = script.main()

        assert rc == 0
        assert body_path.exists()
        output_text = gh_output_path.read_text(encoding="utf-8")
        assert "is_stale=true" in output_text  # both sources missing -> unreadable -> stale
        assert f"title={script.ISSUE_TITLE}" in output_text

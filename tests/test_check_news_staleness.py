"""Tests for scripts/check_news_staleness.py.

2026-11-16: new script -- see its module docstring for why it exists
(active GitHub-issue alerting for stale manual news/시황 input, mirroring
scripts/check_pipeline_health.py's pattern but for the news_days_ago
signal docs/index.html already surfaces passively). Nothing here touches
real repo data; every check is pointed at tmp_path fixtures or
in-memory dicts."""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_news_staleness as script  # noqa: E402


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    header = ["run_ts", "ticker", "date", "close", "news_days_ago"]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


class TestReadNewsDaysAgo:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert script.read_news_days_ago(tmp_path / "missing.csv") == {}

    def test_parses_int_values_per_ticker(self, tmp_path):
        csv_path = tmp_path / "latest.csv"
        _write_csv(
            csv_path,
            [
                {"run_ts": "2026-11-14T01:56:06+00:00", "ticker": "AAPL", "date": "2026-11-13", "close": "356.27", "news_days_ago": "60"},
                {"run_ts": "2026-11-14T01:56:06+00:00", "ticker": "MSFT", "date": "2026-11-13", "close": "523.88", "news_days_ago": "3"},
            ],
        )
        assert script.read_news_days_ago(csv_path) == {"AAPL": 60, "MSFT": 3}

    def test_blank_value_is_none(self, tmp_path):
        csv_path = tmp_path / "latest.csv"
        _write_csv(
            csv_path,
            [{"run_ts": "2026-11-14T01:56:06+00:00", "ticker": "TSLA", "date": "2026-11-13", "close": "390.22", "news_days_ago": ""}],
        )
        assert script.read_news_days_ago(csv_path) == {"TSLA": None}

    def test_unparseable_value_is_none(self, tmp_path):
        csv_path = tmp_path / "latest.csv"
        _write_csv(
            csv_path,
            [{"run_ts": "2026-11-14T01:56:06+00:00", "ticker": "NVDA", "date": "2026-11-13", "close": "162.85", "news_days_ago": "not-a-number"}],
        )
        assert script.read_news_days_ago(csv_path) == {"NVDA": None}

    def test_row_without_ticker_is_skipped(self, tmp_path):
        csv_path = tmp_path / "latest.csv"
        _write_csv(
            csv_path,
            [{"run_ts": "2026-11-14T01:56:06+00:00", "ticker": "", "date": "2026-11-13", "close": "1.0", "news_days_ago": "5"}],
        )
        assert script.read_news_days_ago(csv_path) == {}


class TestBuildReport:
    def test_empty_input_is_not_stale_and_says_so(self):
        report = script.build_report({})
        assert report.is_stale is False
        assert "읽을 수 없어" in report.body

    def test_all_within_window_is_not_stale(self):
        report = script.build_report({"AAPL": 1, "MSFT": 5, "TSLA": script.NEWS_WINDOW_DAYS}, threshold=script.NEWS_WINDOW_DAYS)
        assert report.is_stale is False
        assert "정상 갱신" in report.body

    def test_freshest_one_day_past_threshold_is_stale(self):
        report = script.build_report(
            {"AAPL": script.NEWS_WINDOW_DAYS + 1, "MSFT": 60}, threshold=script.NEWS_WINDOW_DAYS
        )
        assert report.is_stale is True

    def test_mixed_fresh_and_stale_tickers_is_not_stale(self):
        # One recently-updated ticker (or GENERAL) means 현우 is actively
        # using the workflow -- a stale-looking sibling ticker is treated
        # as plausibly just "nothing to report on this one", not a broken
        # workflow. See module docstring's design note.
        report = script.build_report({"AAPL": 2, "MSFT": 60, "NVDA": 90}, threshold=script.NEWS_WINDOW_DAYS)
        assert report.is_stale is False

    def test_never_submitted_anything_is_stale(self):
        report = script.build_report({"AAPL": None, "MSFT": None}, threshold=script.NEWS_WINDOW_DAYS)
        assert report.is_stale is True

    def test_stale_body_lists_each_ticker_with_its_day_count(self):
        report = script.build_report({"AAPL": 60, "MSFT": None}, threshold=script.NEWS_WINDOW_DAYS)
        assert "AAPL" in report.body and "60일 전" in report.body
        assert "MSFT" in report.body and "입력 이력 없음" in report.body

    def test_stale_body_includes_issue_form_link(self):
        report = script.build_report({"AAPL": 60}, threshold=script.NEWS_WINDOW_DAYS)
        assert script.ISSUE_FORM_URL in report.body

    def test_title_is_stable_marker(self):
        assert script.build_report({"AAPL": 1}).title == script.ISSUE_TITLE
        assert script.build_report({"AAPL": 60}).title == script.ISSUE_TITLE

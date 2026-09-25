from datetime import date

from sw1.news.log import (
    NEWS_WINDOW_DAYS,
    append_entry,
    filter_recent_entries,
    latest_entry_date,
    read_log,
)


def test_read_log_missing_file_returns_empty_list(tmp_path):
    assert read_log(tmp_path / "nope.jsonl") == []


def test_append_then_read_round_trips(tmp_path):
    path = tmp_path / "AAPL.jsonl"
    append_entry(path, "2026-09-10", "첫 번째 코멘터리")
    append_entry(path, "2026-09-11", "두 번째 코멘터리")

    entries = read_log(path)
    assert entries == [
        {"date": "2026-09-10", "text": "첫 번째 코멘터리"},
        {"date": "2026-09-11", "text": "두 번째 코멘터리"},
    ]


def test_append_creates_parent_directories(tmp_path):
    path = tmp_path / "market" / "GENERAL.jsonl"
    append_entry(path, "2026-09-10", "일반 시황")
    assert path.exists()
    assert read_log(path) == [{"date": "2026-09-10", "text": "일반 시황"}]


def test_read_log_skips_malformed_lines(tmp_path):
    path = tmp_path / "AAPL.jsonl"
    path.write_text(
        '{"date": "2026-09-10", "text": "정상 항목"}\n'
        "이건 JSON이 아님\n"
        '{"date": "2026-09-11"}\n'  # missing "text"
        '{"text": "날짜 없음"}\n'  # missing "date"
        "\n",  # blank line
        encoding="utf-8",
    )
    entries = read_log(path)
    assert entries == [{"date": "2026-09-10", "text": "정상 항목"}]


def test_filter_recent_entries_keeps_within_window():
    entries = [
        {"date": "2026-08-20", "text": "old"},  # 26 days before as_of -- outside a 14-day window
        {"date": "2026-09-14", "text": "recent"},
        {"date": "2026-09-15", "text": "today"},
    ]
    kept = filter_recent_entries(entries, as_of=date(2026, 9, 15), window_days=14)
    dates = [e["date"] for e in kept]
    assert "2026-09-14" in dates
    assert "2026-09-15" in dates
    assert "2026-08-20" not in dates


def test_filter_recent_entries_boundary_is_inclusive():
    # exactly window_days before as_of should still be kept (cutoff <= d)
    entries = [{"date": "2026-09-01", "text": "exactly at cutoff"}]
    kept = filter_recent_entries(entries, as_of=date(2026, 9, 15), window_days=14)
    assert [e["date"] for e in kept] == ["2026-09-01"]


def test_filter_recent_entries_excludes_future_dates():
    entries = [{"date": "2026-09-20", "text": "future"}]
    kept = filter_recent_entries(entries, as_of=date(2026, 9, 15), window_days=14)
    assert kept == []


def test_filter_recent_entries_drops_unparseable_dates():
    entries = [
        {"date": "not-a-date", "text": "bad"},
        {"date": "2026-09-15", "text": "good"},
    ]
    kept = filter_recent_entries(entries, as_of=date(2026, 9, 15), window_days=14)
    assert kept == [{"date": "2026-09-15", "text": "good"}]


def test_filter_recent_entries_returns_sorted_oldest_first():
    entries = [
        {"date": "2026-09-15", "text": "c"},
        {"date": "2026-09-10", "text": "a"},
        {"date": "2026-09-12", "text": "b"},
    ]
    kept = filter_recent_entries(entries, as_of=date(2026, 9, 15), window_days=14)
    assert [e["text"] for e in kept] == ["a", "b", "c"]


def test_default_window_days_is_14():
    assert NEWS_WINDOW_DAYS == 14


def test_latest_entry_date_empty_list_returns_none():
    assert latest_entry_date([]) is None


def test_latest_entry_date_picks_max_regardless_of_order():
    entries = [
        {"date": "2026-09-10", "text": "a"},
        {"date": "2026-09-15", "text": "c"},
        {"date": "2026-09-12", "text": "b"},
    ]
    assert latest_entry_date(entries) == date(2026, 9, 15)


def test_latest_entry_date_ignores_the_scoring_window():
    # A date far older than any 14-day window is still a valid "latest" if
    # it's the only entry -- staleness reporting must not depend on
    # filter_recent_entries having been applied first.
    entries = [{"date": "2025-01-01", "text": "very old"}]
    assert latest_entry_date(entries) == date(2025, 1, 1)


def test_latest_entry_date_skips_unparseable_dates():
    entries = [
        {"date": "not-a-date", "text": "bad"},
        {"date": "2026-09-10", "text": "good"},
    ]
    assert latest_entry_date(entries) == date(2026, 9, 10)


def test_latest_entry_date_all_unparseable_returns_none():
    entries = [{"date": "not-a-date", "text": "bad"}]
    assert latest_entry_date(entries) is None

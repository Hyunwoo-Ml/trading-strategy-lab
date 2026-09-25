"""Tests for scripts/collect_daily_data.py's news-score wiring.

2026-09-13 (Task #13): initial wiring against a single weekly-overwrite
.txt file per ticker.
2026-09-15: input moved to append-only per-ticker JSONL logs plus a shared
ticker-agnostic market-wide log, both filtered to a recent window before
scoring (see sw1/news/log.py). Only _collect_news_scores() is covered here
-- the rest of main() does live network I/O (yfinance) and isn't
unit-tested, same as before this change."""
import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_daily_data as script  # noqa: E402

TODAY = datetime.now(timezone.utc).date()
TODAY_STR = TODAY.isoformat()
OLD_STR = (TODAY - timedelta(days=30)).isoformat()  # outside the default 14-day window


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    """Points the script's module-level path constants at scratch
    directories so tests never touch the real repo's sw1/news/input or
    data/news, then reloads it fresh."""
    importlib.reload(script)
    data_dir = tmp_path / "data"
    news_input_dir = tmp_path / "news_input"
    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "NEWS_INPUT_DIR", news_input_dir)
    monkeypatch.setattr(script, "NEWS_GENERAL_LOG_PATH", news_input_dir / "market" / "GENERAL.jsonl")
    monkeypatch.setattr(script, "NEWS_CACHE_PATH", data_dir / "news" / "cache.json")
    return script


def write_ticker_entry(mod, ticker, date_str, text):
    path = mod.NEWS_INPUT_DIR / f"{ticker}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date_str, "text": text}, ensure_ascii=False) + "\n")


def write_general_entry(mod, date_str, text):
    path = mod.NEWS_GENERAL_LOG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"date": date_str, "text": text}, ensure_ascii=False) + "\n")


def test_ticker_with_no_input_gets_none(wired_script):
    scores = wired_script._collect_news_scores()
    assert all(v is None for v in scores.values())
    assert set(scores.keys()) == set(wired_script.M7_TICKERS)


def test_ticker_with_only_old_entries_outside_window_gets_none(wired_script):
    write_ticker_entry(wired_script, "AAPL", OLD_STR, "윈도우 밖 오래된 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["AAPL"] is None


def test_missing_api_key_skips_gracefully(wired_script, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write_ticker_entry(wired_script, "NVDA", TODAY_STR, "엔비디아 관련 이번 주 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["NVDA"] is None
    # must not have crashed the run, and must not have written a bogus cache entry
    cache = json.loads(wired_script.NEWS_CACHE_PATH.read_text(encoding="utf-8"))
    assert "NVDA" not in cache


def test_ticker_with_input_and_key_gets_scored(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps([{"category": "실적", "sentiment": 0.7, "summary": "s"}])

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    write_ticker_entry(wired_script, "MSFT", TODAY_STR, "마이크로소프트 실적 관련 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["MSFT"] == pytest.approx(0.7)

    cache = json.loads(wired_script.NEWS_CACHE_PATH.read_text(encoding="utf-8"))
    assert cache["MSFT"]["news_score"] == pytest.approx(0.7)


def test_unchanged_input_reuses_cache_without_calling_llm(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def boom(*args, **kwargs):
        raise AssertionError("should not call the LLM when the windowed view hasn't changed")

    write_ticker_entry(wired_script, "GOOGL", TODAY_STR, "알파벳 이번 주 코멘터리")

    # first run: real (fake) LLM call populates the cache
    monkeypatch.setattr(
        "sw1.news.scorer.call_anthropic",
        lambda prompt, api_key, model="claude-sonnet-4-5": json.dumps(
            [{"category": "실적", "sentiment": 0.5, "summary": "s"}]
        ),
    )
    first_scores = wired_script._collect_news_scores()
    assert first_scores["GOOGL"] == pytest.approx(0.5)

    # second run with the SAME entries: must reuse the cache, not call the LLM
    monkeypatch.setattr("sw1.news.scorer.call_anthropic", boom)
    second_scores = wired_script._collect_news_scores()
    assert second_scores["GOOGL"] == pytest.approx(0.5)


def test_new_entry_appended_triggers_rescoring(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    write_ticker_entry(wired_script, "TSLA", TODAY_STR, "테슬라 코멘터리 1")
    monkeypatch.setattr(
        "sw1.news.scorer.call_anthropic",
        lambda prompt, api_key, model="claude-sonnet-4-5": json.dumps(
            [{"category": "실적", "sentiment": 0.2, "summary": "s"}]
        ),
    )
    wired_script._collect_news_scores()

    # append a second entry (log grows -- never overwritten)
    write_ticker_entry(wired_script, "TSLA", TODAY_STR, "테슬라 코멘터리 2 (추가됨)")
    monkeypatch.setattr(
        "sw1.news.scorer.call_anthropic",
        lambda prompt, api_key, model="claude-sonnet-4-5": json.dumps(
            [{"category": "실적", "sentiment": -0.4, "summary": "s"}]
        ),
    )
    scores = wired_script._collect_news_scores()
    assert scores["TSLA"] == pytest.approx(-0.4)


def test_scoring_error_for_one_ticker_does_not_crash_the_run(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def flaky_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        raise RuntimeError("simulated API error")

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", flaky_call_anthropic)
    write_ticker_entry(wired_script, "META", TODAY_STR, "메타 관련 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["META"] is None  # degraded gracefully, no exception raised


# -- 2026-09-15: ticker-agnostic general-market log --


def test_general_only_entry_can_still_score_a_ticker_with_no_own_log(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        assert "전체 시장 시황" in prompt
        return json.dumps(
            [{"category": "거시경제", "sentiment": 0.3, "summary": "s", "source": "general"}]
        )

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    write_general_entry(wired_script, TODAY_STR, "트럼프가 이란과의 협상 가능성을 언급")
    scores = wired_script._collect_news_scores()
    # every M7 ticker sees the general entry in its prompt (relevance is the model's call)
    assert scores["AAPL"] == pytest.approx(0.3)
    assert scores["TSLA"] == pytest.approx(0.3)


def test_old_general_entry_outside_window_is_not_passed_to_model(wired_script, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not score when only an out-of-window entry exists")

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", boom)
    write_general_entry(wired_script, OLD_STR, "윈도우 밖 오래된 일반 시황")
    scores = wired_script._collect_news_scores()
    assert all(v is None for v in scores.values())


def test_ticker_and_general_entries_are_both_fed_to_the_same_call(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    seen_prompts = []

    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        seen_prompts.append(prompt)
        return json.dumps([{"category": "실적", "sentiment": 0.1, "summary": "s"}])

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    write_ticker_entry(wired_script, "NVDA", TODAY_STR, "엔비디아 자체 코멘터리")
    write_general_entry(wired_script, TODAY_STR, "반도체 수출 규제 관련 일반 시황")
    wired_script._collect_news_scores()

    nvda_prompt = next(p for p in seen_prompts if "엔비디아 자체 코멘터리" in p)
    assert "반도체 수출 규제 관련 일반 시황" in nvda_prompt


# -- 2026-09-25: _collect_news_staleness (dashboard "N일간 뉴스 입력 없음" signal) --


def test_staleness_none_when_ticker_never_had_any_entry(wired_script):
    staleness = wired_script._collect_news_staleness(TODAY)
    assert all(v is None for v in staleness.values())
    assert set(staleness.keys()) == set(wired_script.M7_TICKERS)


def test_staleness_counts_days_since_own_ticker_entry(wired_script):
    five_days_ago = (TODAY - timedelta(days=5)).isoformat()
    write_ticker_entry(wired_script, "AAPL", five_days_ago, "닷새 전 코멘터리")
    staleness = wired_script._collect_news_staleness(TODAY)
    assert staleness["AAPL"] == 5


def test_staleness_ignores_the_14_day_scoring_window(wired_script):
    # Unlike _collect_news_scores, this must keep counting well past 14 days
    # -- that's exactly the case a "add fresh news" reminder is for.
    write_ticker_entry(wired_script, "AAPL", OLD_STR, "30일 전 코멘터리 (윈도우 밖)")
    staleness = wired_script._collect_news_staleness(TODAY)
    assert staleness["AAPL"] == 30


def test_staleness_uses_whichever_log_is_more_recent(wired_script):
    ten_days_ago = (TODAY - timedelta(days=10)).isoformat()
    two_days_ago = (TODAY - timedelta(days=2)).isoformat()
    write_ticker_entry(wired_script, "MSFT", ten_days_ago, "티커 자체 코멘터리 (10일 전)")
    write_general_entry(wired_script, two_days_ago, "일반 시황 (2일 전, 더 최근)")
    staleness = wired_script._collect_news_staleness(TODAY)
    assert staleness["MSFT"] == 2


def test_staleness_general_only_still_counts_for_every_ticker(wired_script):
    three_days_ago = (TODAY - timedelta(days=3)).isoformat()
    write_general_entry(wired_script, three_days_ago, "일반 시황만 있음")
    staleness = wired_script._collect_news_staleness(TODAY)
    assert staleness["AAPL"] == 3
    assert staleness["TSLA"] == 3


def test_staleness_zero_for_todays_entry(wired_script):
    write_ticker_entry(wired_script, "NVDA", TODAY_STR, "오늘 입력된 코멘터리")
    staleness = wired_script._collect_news_staleness(TODAY)
    assert staleness["NVDA"] == 0

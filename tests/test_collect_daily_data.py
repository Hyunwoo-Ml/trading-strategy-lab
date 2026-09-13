"""Tests for scripts/collect_daily_data.py's news-score wiring (Task #13,
2026-09-13). Only _collect_news_scores() is covered here -- the rest of
main() does live network I/O (yfinance) and isn't unit-tested, same as
before this change."""
import importlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import collect_daily_data as script  # noqa: E402


@pytest.fixture
def wired_script(tmp_path, monkeypatch):
    """Points the script's module-level path constants at scratch
    directories so tests never touch the real repo's sw1/news/input or
    data/news, then reloads it fresh."""
    importlib.reload(script)
    data_dir = tmp_path / "data"
    monkeypatch.setattr(script, "DATA_DIR", data_dir)
    monkeypatch.setattr(script, "NEWS_INPUT_DIR", tmp_path / "news_input")
    monkeypatch.setattr(script, "NEWS_CACHE_PATH", data_dir / "news" / "cache.json")
    return script


def write_input(mod, ticker, text):
    mod.NEWS_INPUT_DIR.mkdir(parents=True, exist_ok=True)
    (mod.NEWS_INPUT_DIR / f"{ticker}.txt").write_text(text, encoding="utf-8")


def test_ticker_with_no_input_file_gets_none(wired_script):
    scores = wired_script._collect_news_scores()
    assert all(v is None for v in scores.values())
    assert set(scores.keys()) == set(wired_script.M7_TICKERS)


def test_ticker_with_empty_input_file_gets_none(wired_script):
    write_input(wired_script, "AAPL", "   \n  ")
    scores = wired_script._collect_news_scores()
    assert scores["AAPL"] is None


def test_missing_api_key_skips_gracefully(wired_script, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write_input(wired_script, "NVDA", "엔비디아 관련 이번 주 코멘터리")
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
    write_input(wired_script, "MSFT", "마이크로소프트 실적 관련 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["MSFT"] == pytest.approx(0.7)

    cache = json.loads(wired_script.NEWS_CACHE_PATH.read_text(encoding="utf-8"))
    assert cache["MSFT"]["news_score"] == pytest.approx(0.7)


def test_unchanged_input_reuses_cache_without_calling_llm(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")

    def boom(*args, **kwargs):
        raise AssertionError("should not call the LLM when input text hasn't changed")

    write_input(wired_script, "GOOGL", "알파벳 이번 주 코멘터리")

    # first run: real (fake) LLM call populates the cache
    monkeypatch.setattr(
        "sw1.news.scorer.call_anthropic",
        lambda prompt, api_key, model="claude-sonnet-4-5": json.dumps(
            [{"category": "실적", "sentiment": 0.5, "summary": "s"}]
        ),
    )
    first_scores = wired_script._collect_news_scores()
    assert first_scores["GOOGL"] == pytest.approx(0.5)

    # second run with the SAME input text: must reuse the cache, not call the LLM
    monkeypatch.setattr("sw1.news.scorer.call_anthropic", boom)
    second_scores = wired_script._collect_news_scores()
    assert second_scores["GOOGL"] == pytest.approx(0.5)


def test_changed_input_triggers_rescoring(wired_script, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-test")
    write_input(wired_script, "TSLA", "테슬라 지난주 코멘터리")
    monkeypatch.setattr(
        "sw1.news.scorer.call_anthropic",
        lambda prompt, api_key, model="claude-sonnet-4-5": json.dumps(
            [{"category": "실적", "sentiment": 0.2, "summary": "s"}]
        ),
    )
    wired_script._collect_news_scores()

    write_input(wired_script, "TSLA", "테슬라 이번 주 새로운 코멘터리 (변경됨)")
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
    write_input(wired_script, "META", "메타 관련 코멘터리")
    scores = wired_script._collect_news_scores()
    assert scores["META"] is None  # degraded gracefully, no exception raised

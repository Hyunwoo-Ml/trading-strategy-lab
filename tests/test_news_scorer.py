import json

import pytest

from sw1.news.scorer import (
    AnthropicKeyMissing,
    NEWS_CATEGORIES,
    build_news_scoring_prompt,
    entries_hash,
    parse_news_scoring_response,
    score_news_for_ticker,
    score_ticker_if_changed,
)


def _entry(date, text):
    return {"date": date, "text": text}


def test_prompt_mentions_ticker_and_categories():
    prompt = build_news_scoring_prompt(
        "NVDA", [_entry("2026-09-10", "엔비디아 실적 발표...")], []
    )
    assert "NVDA" in prompt
    for category in NEWS_CATEGORIES:
        assert category in prompt


def test_prompt_includes_both_ticker_and_general_blocks():
    prompt = build_news_scoring_prompt(
        "NVDA",
        [_entry("2026-09-10", "엔비디아 자체 코멘터리")],
        [_entry("2026-09-11", "트럼프의 이란 관련 발언")],
    )
    assert "엔비디아 자체 코멘터리" in prompt
    assert "트럼프의 이란 관련 발언" in prompt
    assert "종목별 코멘터리" in prompt
    assert "전체 시장 시황" in prompt


def test_prompt_shows_placeholder_for_empty_blocks():
    prompt = build_news_scoring_prompt("AAPL", [], [])
    assert "(없음)" in prompt


def test_parse_empty_array_gives_neutral_score():
    result = parse_news_scoring_response("[]", ticker="AAPL")
    assert result.news_score == 0.0
    assert result.items == []


def test_parse_single_positive_earnings_item():
    raw = json.dumps(
        [
            {
                "category": "실적",
                "sentiment": 0.8,
                "summary": "예상치를 상회하는 실적 발표",
                "reasoning": "매출/이익 모두 컨센서스 상회",
                "source": "ticker",
                "date": "2026-09-10",
            }
        ]
    )
    result = parse_news_scoring_response(raw, ticker="MSFT")
    assert result.news_score == pytest.approx(0.8)
    assert result.items[0].category == "실적"
    assert result.items[0].source == "ticker"


def test_parse_weights_categories_by_importance():
    # 실적 (weight 1.0) should dominate 기타 (weight 0.3) in the blend
    raw = json.dumps(
        [
            {"category": "실적", "sentiment": 1.0, "summary": "a"},
            {"category": "기타", "sentiment": -1.0, "summary": "b"},
        ]
    )
    result = parse_news_scoring_response(raw, ticker="TSLA")
    assert result.news_score > 0  # dominated by the higher-weight positive item


def test_parse_unknown_category_falls_back_to_other():
    raw = json.dumps([{"category": "존재하지않는카테고리", "sentiment": 0.5, "summary": "x"}])
    result = parse_news_scoring_response(raw, ticker="AMZN")
    assert result.items[0].category == "기타"


def test_parse_clamps_out_of_range_sentiment():
    raw = json.dumps([{"category": "실적", "sentiment": 5.0, "summary": "x"}])
    result = parse_news_scoring_response(raw, ticker="GOOGL")
    assert result.items[0].sentiment == 1.0


def test_parse_defaults_source_to_ticker_when_missing():
    raw = json.dumps([{"category": "실적", "sentiment": 0.1, "summary": "x"}])
    result = parse_news_scoring_response(raw, ticker="GOOGL")
    assert result.items[0].source == "ticker"


def test_parse_falls_back_to_ticker_for_unrecognized_source():
    raw = json.dumps([{"category": "실적", "sentiment": 0.1, "summary": "x", "source": "이상한값"}])
    result = parse_news_scoring_response(raw, ticker="GOOGL")
    assert result.items[0].source == "ticker"


def test_parse_accepts_general_source():
    raw = json.dumps(
        [{"category": "거시경제", "sentiment": 0.2, "summary": "x", "source": "general", "date": "2026-09-11"}]
    )
    result = parse_news_scoring_response(raw, ticker="NVDA")
    assert result.items[0].source == "general"
    assert result.items[0].date == "2026-09-11"


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        parse_news_scoring_response("이것은 JSON이 아닙니다", ticker="META")


def test_parse_rejects_non_array_json():
    with pytest.raises(ValueError):
        parse_news_scoring_response('{"not": "an array"}', ticker="META")


def test_score_news_for_ticker_raises_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicKeyMissing):
        score_news_for_ticker("AAPL", [_entry("2026-09-10", "some commentary")], [], api_key=None)


def test_score_news_for_ticker_uses_mocked_llm_call(monkeypatch):
    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps(
            [{"category": "제품_기술", "sentiment": 0.4, "summary": "신제품 발표", "reasoning": "긍정적 반응"}]
        )

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    result = score_news_for_ticker(
        "NVDA", [_entry("2026-09-10", "신제품 관련 코멘터리")], [], api_key="fake-key-for-test"
    )
    assert result.ticker == "NVDA"
    assert result.news_score == pytest.approx(0.4)


# -- 2026-09-15: entries_hash / score_ticker_if_changed on windowed entry lists --


def test_entries_hash_ignores_ordering_within_each_list_is_stable():
    a = entries_hash([_entry("2026-09-10", "x")], [_entry("2026-09-11", "y")])
    b = entries_hash([_entry("2026-09-10", "x")], [_entry("2026-09-11", "y")])
    assert a == b


def test_entries_hash_changes_when_ticker_entries_change():
    a = entries_hash([_entry("2026-09-10", "x")], [])
    b = entries_hash([_entry("2026-09-10", "y")], [])
    assert a != b


def test_entries_hash_changes_when_general_entries_change():
    a = entries_hash([], [_entry("2026-09-10", "x")])
    b = entries_hash([], [_entry("2026-09-10", "y")])
    assert a != b


def test_score_ticker_if_changed_both_empty_returns_none_without_scoring(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not call the LLM when nothing is in window")

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", boom)
    entry, freshly_scored = score_ticker_if_changed("AAPL", [], [], cached_entry=None, api_key="k")
    assert entry is None
    assert freshly_scored is False


def test_score_ticker_if_changed_reuses_cache_when_window_unchanged(monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("should not re-call the LLM when the windowed view hasn't changed")

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", boom)
    ticker_entries = [_entry("2026-09-10", "이번 주 실적 발표 관련 코멘터리")]
    cached = {
        "entries_hash": entries_hash(ticker_entries, []),
        "news_score": 0.55,
        "items": [],
    }

    entry, freshly_scored = score_ticker_if_changed(
        "MSFT", ticker_entries, [], cached_entry=cached, api_key="k"
    )
    assert entry == cached
    assert freshly_scored is False


def test_score_ticker_if_changed_calls_llm_when_entries_are_new(monkeypatch):
    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps([{"category": "실적", "sentiment": 0.6, "summary": "s", "reasoning": "r"}])

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    ticker_entries = [_entry("2026-09-10", "새로운 코멘터리 텍스트")]
    entry, freshly_scored = score_ticker_if_changed(
        "GOOGL", ticker_entries, [], cached_entry=None, api_key="k"
    )
    assert freshly_scored is True
    assert entry["news_score"] == pytest.approx(0.6)
    assert entry["entries_hash"] == entries_hash(ticker_entries, [])


def test_score_ticker_if_changed_calls_llm_when_ticker_entries_changed_from_cache(monkeypatch):
    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps([{"category": "실적", "sentiment": -0.3, "summary": "s"}])

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    old_cached = {
        "entries_hash": entries_hash([_entry("2026-09-03", "지난주 텍스트")], []),
        "news_score": 0.9,
        "items": [],
    }
    entry, freshly_scored = score_ticker_if_changed(
        "AMZN",
        [_entry("2026-09-10", "이번 주 새 텍스트 (지난주와 다름)")],
        [],
        cached_entry=old_cached,
        api_key="k",
    )
    assert freshly_scored is True
    assert entry["news_score"] == pytest.approx(-0.3)


def test_score_ticker_if_changed_calls_llm_when_only_general_entries_change(monkeypatch):
    """A ticker with no dedicated commentary at all can still get scored
    if there's something in the general-market log -- the model decides
    relevance, not this function."""

    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps(
            [{"category": "거시경제", "sentiment": 0.2, "summary": "s", "source": "general"}]
        )

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    entry, freshly_scored = score_ticker_if_changed(
        "TSLA", [], [_entry("2026-09-11", "관세 관련 일반 시황")], cached_entry=None, api_key="k"
    )
    assert freshly_scored is True
    assert entry["news_score"] == pytest.approx(0.2)


def test_score_ticker_if_changed_rescores_when_general_entries_age_out_of_window(monkeypatch):
    """Simulates the caller re-filtering to a smaller window between runs
    (an entry aged out) -- even with no new entries added, the changed
    windowed view should be treated as "changed"."""

    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps([{"category": "기타", "sentiment": 0.0, "summary": "s"}])

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    stale_cached = {
        "entries_hash": entries_hash([], [_entry("2026-08-01", "오래된 일반 시황")]),
        "news_score": 0.4,
        "items": [],
    }
    # caller already dropped the aged-out entry before calling this function
    entry, freshly_scored = score_ticker_if_changed(
        "META", [], [], cached_entry=stale_cached, api_key="k"
    )
    # both lists now empty -> no scoring at all, distinct from a rescoring case
    assert entry is None
    assert freshly_scored is False


def test_score_ticker_if_changed_propagates_missing_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicKeyMissing):
        score_ticker_if_changed(
            "TSLA", [_entry("2026-09-10", "키 없이 스코어링 시도")], [], cached_entry=None, api_key=None
        )

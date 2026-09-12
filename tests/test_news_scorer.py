import json

import pytest

from sw1.news.scorer import (
    AnthropicKeyMissing,
    NEWS_CATEGORIES,
    build_news_scoring_prompt,
    parse_news_scoring_response,
    score_news_for_ticker,
)


def test_prompt_mentions_ticker_and_categories():
    prompt = build_news_scoring_prompt("NVDA", "엔비디아 실적 발표...")
    assert "NVDA" in prompt
    for category in NEWS_CATEGORIES:
        assert category in prompt


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
            }
        ]
    )
    result = parse_news_scoring_response(raw, ticker="MSFT")
    assert result.news_score == pytest.approx(0.8)
    assert result.items[0].category == "실적"


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


def test_parse_rejects_non_json():
    with pytest.raises(ValueError):
        parse_news_scoring_response("이것은 JSON이 아닙니다", ticker="META")


def test_parse_rejects_non_array_json():
    with pytest.raises(ValueError):
        parse_news_scoring_response('{"not": "an array"}', ticker="META")


def test_score_news_for_ticker_raises_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AnthropicKeyMissing):
        score_news_for_ticker("AAPL", "some weekly commentary text", api_key=None)


def test_score_news_for_ticker_uses_mocked_llm_call(monkeypatch):
    def fake_call_anthropic(prompt, api_key, model="claude-sonnet-4-5"):
        return json.dumps(
            [{"category": "제품_기술", "sentiment": 0.4, "summary": "신제품 발표", "reasoning": "긍정적 반응"}]
        )

    monkeypatch.setattr("sw1.news.scorer.call_anthropic", fake_call_anthropic)
    result = score_news_for_ticker("NVDA", "신제품 관련 코멘터리", api_key="fake-key-for-test")
    assert result.ticker == "NVDA"
    assert result.news_score == pytest.approx(0.4)


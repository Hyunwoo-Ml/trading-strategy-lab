"""
SW1 -- news sentiment scoring module.

Turns a block of weekly Korean market-commentary text (the user pastes this
in manually, once a week -- see project README) into a per-category
sentiment breakdown and one aggregate news_score in [-1, 1], suitable for
sw1.scoring.integrate.compute_integrated_score's `news_score` argument.

STATUS (2026-09-13): ANTHROPIC_API_KEY is registered as a GitHub Actions
secret and scripts/collect_daily_data.py._collect_news_scores() now calls
score_ticker_if_changed() below for every M7 ticker that has a weekly
commentary file under sw1/news/input/{TICKER}.txt (see that directory's
README for the paste format). A ticker with no input file, an unchanged
input since the last run (cached score reused -- avoids re-billing the
same weekly paste every weekday), a missing key, or any scoring error all
degrade to news_score=None for that ticker rather than failing the run.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field

# Category -> importance weight. Mirrors the "이벤트 중요도에 따라 가중치를
# 부여" idea from the original 자소서 -- these specific weights are this
# build's own defaults, tune once there's real comparison data from SW2.
NEWS_CATEGORIES: dict[str, float] = {
    "실적": 1.0,          # earnings / guidance
    "규제_소송": 0.9,      # regulatory action, antitrust, litigation
    "제품_기술": 0.8,      # product launches, tech breakthroughs, recalls
    "경영진_지배구조": 0.6,  # management changes, governance events
    "거시경제": 0.5,        # rates, macro data, sector-wide news
    "기타": 0.3,
}


class AnthropicKeyMissing(RuntimeError):
    """Raised when score_news_for_ticker() is called but ANTHROPIC_API_KEY
    isn't set. This is expected until that key is registered (see project
    README / Task #4) -- callers should catch this and skip the news side
    of the score rather than crash the whole pipeline."""


@dataclass
class NewsItem:
    category: str
    sentiment: float  # [-1, 1]
    summary: str
    reasoning: str = ""


@dataclass
class NewsScoreResult:
    ticker: str
    items: list[NewsItem] = field(default_factory=list)
    news_score: float = 0.0  # weighted-average aggregate, [-1, 1]

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "news_score": self.news_score,
            "items": [
                {
                    "category": i.category,
                    "sentiment": i.sentiment,
                    "summary": i.summary,
                    "reasoning": i.reasoning,
                }
                for i in self.items
            ],
        }


def build_news_scoring_prompt(ticker: str, weekly_text: str) -> str:
    """Builds the prompt sent to the LLM. Asks for strict JSON so
    parse_news_scoring_response() can parse it deterministically."""
    categories = ", ".join(NEWS_CATEGORIES.keys())
    return f"""다음은 {ticker}에 대한 이번 주 국내 시황/뉴스 코멘터리입니다.

---
{weekly_text}
---

이 텍스트에서 {ticker}와 직접 관련된 개별 이벤트를 모두 찾아, 각 이벤트를
다음 카테고리 중 하나로 분류하고 감성 점수를 매기세요: {categories}

각 이벤트에 대해 다음 JSON 스키마를 따르는 배열로만 응답하세요 (설명 없이 JSON만):

[
  {{
    "category": "카테고리명 (위 목록 중 하나)",
    "sentiment": -1.0에서 1.0 사이의 숫자 (부정적일수록 -1에 가깝게, 긍정적일수록 1에 가깝게),
    "summary": "이벤트 한 줄 요약",
    "reasoning": "이 감성 점수를 매긴 이유 한두 문장"
  }}
]

관련 이벤트가 없으면 빈 배열 []을 반환하세요."""


def parse_news_scoring_response(raw_text: str, ticker: str) -> NewsScoreResult:
    """Parses the LLM's JSON array response into a NewsScoreResult with a
    weighted-average news_score. Unknown categories fall back to '기타'."""
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"news scorer response wasn't valid JSON: {exc}\nraw: {raw_text!r}") from exc

    if not isinstance(parsed, list):
        raise ValueError(f"expected a JSON array, got {type(parsed).__name__}")

    items: list[NewsItem] = []
    for entry in parsed:
        category = entry.get("category", "기타")
        if category not in NEWS_CATEGORIES:
            category = "기타"
        sentiment = max(-1.0, min(1.0, float(entry.get("sentiment", 0.0))))
        items.append(
            NewsItem(
                category=category,
                sentiment=sentiment,
                summary=entry.get("summary", ""),
                reasoning=entry.get("reasoning", ""),
            )
        )

    if not items:
        return NewsScoreResult(ticker=ticker, items=[], news_score=0.0)

    weight_sum = sum(NEWS_CATEGORIES[i.category] for i in items)
    weighted = sum(i.sentiment * NEWS_CATEGORIES[i.category] for i in items)
    news_score = weighted / weight_sum if weight_sum else 0.0
    news_score = max(-1.0, min(1.0, news_score))

    return NewsScoreResult(ticker=ticker, items=items, news_score=news_score)


def call_anthropic(prompt: str, api_key: str, model: str = "claude-sonnet-4-5") -> str:
    """Thin wrapper around the Anthropic SDK so tests can monkeypatch this
    one function instead of the whole client. Imports `anthropic` lazily
    so the rest of this module works (and is testable) without the
    package installed in every environment."""
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        max_tokens=2048,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text


def score_news_for_ticker(ticker: str, weekly_text: str, api_key: str | None = None) -> NewsScoreResult:
    """End-to-end: build prompt -> call Anthropic -> parse response.

    Raises AnthropicKeyMissing if no key is available (neither passed in
    nor in the ANTHROPIC_API_KEY env var) -- this is the expected state
    until Task #4 registers the key."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AnthropicKeyMissing(
            "ANTHROPIC_API_KEY isn't set -- news scoring is stubbed out until it is "
            "(see sw1/news/scorer.py docstring / project Task #4)."
        )
    prompt = build_news_scoring_prompt(ticker, weekly_text)
    raw_text = call_anthropic(prompt, api_key=key)
    return parse_news_scoring_response(raw_text, ticker=ticker)


def text_hash(weekly_text: str) -> str:
    """Content hash used to detect whether a ticker's pasted weekly
    commentary has actually changed since the last run (see
    score_ticker_if_changed). Whitespace at the edges doesn't count as a
    change -- only the stripped content does."""
    return hashlib.sha256(weekly_text.strip().encode("utf-8")).hexdigest()


def score_ticker_if_changed(
    ticker: str,
    weekly_text: str,
    cached_entry: dict | None = None,
    api_key: str | None = None,
) -> tuple[dict | None, bool]:
    """Decides whether `ticker` needs a fresh LLM call today, given this
    run's pasted weekly commentary and (if any) the cache entry a
    previous run stored for it.

    Returns (cache_entry_or_None, was_freshly_scored):
      - weekly_text is empty/whitespace-only -> (None, False). No input
        for this ticker today; caller should leave news_score as None.
      - weekly_text's content hash matches cached_entry's -> the same
        weekly paste is still sitting there (the whole point is it's
        updated only about once a week, not every weekday the scheduler
        happens to run) -> (cached_entry, False), reusing the previous
        score with no Anthropic call.
      - New or changed weekly_text -> calls score_news_for_ticker and
        returns (fresh_cache_entry, True). Propagates AnthropicKeyMissing
        and any other scoring error so the caller can log + skip without
        this function silently hiding a real failure.
    """
    stripped = weekly_text.strip()
    if not stripped:
        return None, False

    h = text_hash(stripped)
    if cached_entry and cached_entry.get("text_hash") == h:
        return cached_entry, False

    result = score_news_for_ticker(ticker, stripped, api_key=api_key)
    fresh_entry = {
        "text_hash": h,
        "news_score": result.news_score,
        "items": [
            {"category": i.category, "sentiment": i.sentiment, "summary": i.summary}
            for i in result.items
        ],
    }
    return fresh_entry, True

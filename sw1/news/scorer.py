"""
SW1 -- news sentiment scoring module.

Turns accumulated Korean market-commentary entries into a per-category
sentiment breakdown and one aggregate news_score in [-1, 1], suitable for
sw1.scoring.integrate.compute_integrated_score's `news_score` argument.

STATUS (2026-09-15): input moved from a single weekly-overwrite
sw1/news/input/{TICKER}.txt file to an append-only per-ticker log
(sw1/news/input/{TICKER}.jsonl) plus one shared ticker-agnostic log
(sw1/news/input/market/GENERAL.jsonl) for market-wide commentary that
doesn't belong to any single ticker (e.g. a macro/policy headline). See
sw1/news/log.py for the log format and the recent-window filter.

Each scoring call now gets two inputs for a ticker: that ticker's own
recent entries, and the recent general-market entries. The LLM itself
decides which general entries are actually relevant to this ticker and
folds only those into the score -- an irrelevant general entry (e.g. a
healthcare regulation headline scored while looking at NVDA) is expected
to be ignored by the model rather than pre-filtered by code, since
relevance here is a judgment call, not a keyword match.

scripts/collect_daily_data.py._collect_news_scores() calls
score_ticker_if_changed() below for every M7 ticker. A ticker with no
entries in window (neither its own nor general), an unchanged input since
the last run (cached score reused -- avoids re-billing the same entries
every weekday the pipeline happens to run), a missing key, or any scoring
error all degrade to news_score=None for that ticker rather than failing
the run.
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

NEWS_SOURCES = ("ticker", "general")


class AnthropicKeyMissing(RuntimeError):
    """Raised when score_news_for_ticker() is called but ANTHROPIC_API_KEY
    isn't set. Callers should catch this and skip the news side of the
    score rather than crash the whole pipeline."""


@dataclass
class NewsItem:
    category: str
    sentiment: float  # [-1, 1]
    summary: str
    reasoning: str = ""
    source: str = "ticker"  # "ticker" (종목별 코멘터리) or "general" (종목무관 일반 시황)
    date: str = ""


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
                    "source": i.source,
                    "date": i.date,
                }
                for i in self.items
            ],
        }


def _format_entries_block(entries: list[dict]) -> str:
    if not entries:
        return "(없음)"
    return "\n\n".join(f"[{e['date']}] {e['text']}" for e in entries)


def build_news_scoring_prompt(
    ticker: str, ticker_entries: list[dict], general_entries: list[dict]
) -> str:
    """Builds the prompt sent to the LLM. Asks for strict JSON so
    parse_news_scoring_response() can parse it deterministically.

    Two blocks are given: entries pasted specifically for `ticker`, and
    entries from the ticker-agnostic general-market log. The model is
    explicitly asked to first decide which general entries are actually
    relevant to `ticker` and ignore the rest -- that relevance judgment is
    the whole point of feeding both blocks into one call instead of
    pre-filtering with keyword matching in code."""
    categories = ", ".join(NEWS_CATEGORIES.keys())
    ticker_block = _format_entries_block(ticker_entries)
    general_block = _format_entries_block(general_entries)
    return f"""다음은 {ticker}에 대한 최근 뉴스/시황 자료입니다. 두 종류가 있습니다.

[종목별 코멘터리 -- {ticker}에 대해 직접 입력된 내용, 날짜순]
{ticker_block}

[전체 시장 시황 -- 특정 종목과 무관하게 입력된 일반 뉴스/시황, 날짜순]
{general_block}

먼저 "전체 시장 시황" 항목 중에서 {ticker}에 실질적으로 영향을 줄 수 있다고
판단되는 것만 골라내세요 (예: 반도체 수출 규제 뉴스는 반도체 관련주에는
관련 있지만 무관한 업종에는 관련 없다고 판단할 수 있습니다). 관련 없다고
판단한 일반 시황 항목은 무시하고 아래 점수화에 포함하지 마세요.

그 다음, "종목별 코멘터리" 전체와 방금 관련 있다고 골라낸 일반 시황 항목을
합쳐서 {ticker}와 관련된 개별 이벤트를 모두 찾아, 각 이벤트를 다음 카테고리
중 하나로 분류하고 감성 점수를 매기세요: {categories}

각 이벤트에 대해 다음 JSON 스키마를 따르는 배열로만 응답하세요 (설명 없이 JSON만):

[
  {{
    "category": "카테고리명 (위 목록 중 하나)",
    "sentiment": -1.0에서 1.0 사이의 숫자 (부정적일수록 -1에 가깝게, 긍정적일수록 1에 가깝게),
    "summary": "이벤트 한 줄 요약",
    "reasoning": "이 감성 점수를 매긴 이유 한두 문장 (일반 시황에서 가져온 경우 왜 {ticker}와 관련 있다고 판단했는지도 포함)",
    "source": "ticker" 또는 "general" (이 이벤트가 종목별 코멘터리에서 왔는지, 일반 시황에서 왔는지),
    "date": "이 이벤트가 나온 항목의 날짜 (YYYY-MM-DD)"
  }}
]

관련 이벤트가 없으면 빈 배열 []을 반환하세요."""


def parse_news_scoring_response(raw_text: str, ticker: str) -> NewsScoreResult:
    """Parses the LLM's JSON array response into a NewsScoreResult with a
    weighted-average news_score. Unknown categories fall back to '기타'.
    An unrecognized/missing source falls back to 'ticker' (the more
    conservative assumption -- treat it as directly about this ticker
    rather than silently dropping the item)."""
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
        source = entry.get("source", "ticker")
        if source not in NEWS_SOURCES:
            source = "ticker"
        items.append(
            NewsItem(
                category=category,
                sentiment=sentiment,
                summary=entry.get("summary", ""),
                reasoning=entry.get("reasoning", ""),
                source=source,
                date=entry.get("date", ""),
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


def score_news_for_ticker(
    ticker: str,
    ticker_entries: list[dict],
    general_entries: list[dict],
    api_key: str | None = None,
) -> NewsScoreResult:
    """End-to-end: build prompt -> call Anthropic -> parse response.

    Raises AnthropicKeyMissing if no key is available (neither passed in
    nor in the ANTHROPIC_API_KEY env var)."""
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise AnthropicKeyMissing(
            "ANTHROPIC_API_KEY isn't set -- news scoring is stubbed out until it is "
            "(see sw1/news/scorer.py docstring)."
        )
    prompt = build_news_scoring_prompt(ticker, ticker_entries, general_entries)
    raw_text = call_anthropic(prompt, api_key=key)
    return parse_news_scoring_response(raw_text, ticker=ticker)


def entries_hash(ticker_entries: list[dict], general_entries: list[dict]) -> str:
    """Content hash used to detect whether the windowed view of entries
    (this ticker's own + general market) has actually changed since the
    last run -- either because a new entry was added, or because an old
    entry aged out of the recent-N-day window (see sw1.news.log). Either
    case means the model would see a different picture today, so it's
    treated as "changed" and rescored."""
    payload = json.dumps(
        {"ticker": ticker_entries, "general": general_entries},
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def score_ticker_if_changed(
    ticker: str,
    ticker_entries: list[dict],
    general_entries: list[dict],
    cached_entry: dict | None = None,
    api_key: str | None = None,
) -> tuple[dict | None, bool]:
    """Decides whether `ticker` needs a fresh LLM call today, given this
    run's windowed entries (both already filtered to the recent-N-day
    window by the caller -- see sw1.news.log.filter_recent_entries) and
    (if any) the cache entry a previous run stored for it.

    Returns (cache_entry_or_None, was_freshly_scored):
      - Both ticker_entries and general_entries are empty -> (None, False).
        Nothing in view for this ticker today; caller should leave
        news_score as None.
      - The combined windowed view's hash matches cached_entry's -> same
        picture as last run (same entries still in window, window hasn't
        rolled past anything, nothing new added) -> (cached_entry, False),
        reusing the previous score with no Anthropic call.
      - Changed view -> calls score_news_for_ticker and returns
        (fresh_cache_entry, True). Propagates AnthropicKeyMissing and any
        other scoring error so the caller can log + skip without this
        function silently hiding a real failure.

    Note: general_entries alone (no ticker-specific entries at all) can
    still trigger a fresh score -- a ticker with no dedicated commentary
    can still be judged relevant to a general-market item by the model."""
    if not ticker_entries and not general_entries:
        return None, False

    h = entries_hash(ticker_entries, general_entries)
    if cached_entry and cached_entry.get("entries_hash") == h:
        return cached_entry, False

    result = score_news_for_ticker(ticker, ticker_entries, general_entries, api_key=api_key)
    fresh_entry = {
        "entries_hash": h,
        "news_score": result.news_score,
        "items": [
            {
                "category": i.category,
                "sentiment": i.sentiment,
                "summary": i.summary,
                "source": i.source,
                "date": i.date,
            }
            for i in result.items
        ],
    }
    return fresh_entry, True

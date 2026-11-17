from __future__ import annotations

from datetime import datetime, timezone

from sw1.market.news import impact_of, importance_of, merge, news_tone, parse_rss, parse_yahoo_news, topics_of

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>Fed</title>
<item><title>Federal Reserve issues FOMC statement</title><link>https://fed.example/a</link>
<pubDate>Wed, 29 Oct 2026 18:00:00 GMT</pubDate></item>
<item><title>Minutes of the Federal Open Market Committee</title><link>https://fed.example/b</link>
<pubDate>Mon, 16 Nov 2026 19:00:00 GMT</pubDate></item>
<item><title>No date here</title><link>https://fed.example/c</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>CPI rises 0.3 percent in October</title><link href="https://bls.example/cpi"/>
<updated>2026-11-13T13:30:00Z</updated></entry></feed>"""


def test_topics_and_impact_rules():
    assert set(topics_of("Fed's Powell signals rate cut as inflation cools")) >= {"연준", "물가"}
    assert "유가" in topics_of("Oil jumps as OPEC extends cuts")
    assert "지정학" in topics_of("Iran tensions lift crude")
    assert impact_of("Nvidia beats estimates, raises guidance") == "positive"
    assert impact_of("Tesla shares plunge after deliveries miss") == "negative"
    assert impact_of("Apple to hold event on Tuesday") == "neutral"
    assert importance_of(1, ["연준"]) == 2 and importance_of(2, ["물가"]) == 3 and importance_of(1, ["AI"]) == 1


def test_parse_rss_and_atom():
    items = parse_rss(RSS, "연준", 2)
    assert [i["link"] for i in items] == ["https://fed.example/a", "https://fed.example/b"]  # undated entry skipped
    assert items[1]["time"] == "2026-11-16T19:00:00Z" and items[1]["kind"] == "macro"
    atom = parse_rss(ATOM, "미 노동통계국", 2)
    assert atom[0]["link"] == "https://bls.example/cpi" and "물가" in atom[0]["topics"] and atom[0]["importance"] == 3
    assert parse_rss("not xml", "x", 1) == []


def test_parse_yahoo_news_both_formats():
    new = [{"id": "1", "content": {"title": "Nvidia surges on AI demand", "pubDate": "2026-11-17T10:00:00Z",
                                   "provider": {"displayName": "Reuters"}, "canonicalUrl": {"url": "https://y.example/1"}}}]
    old = [{"title": "Nvidia shares fall", "publisher": "Bloomberg", "link": "https://y.example/2",
            "providerPublishTime": int(datetime(2026, 11, 16, 9, tzinfo=timezone.utc).timestamp())}]
    a = parse_yahoo_news(new, "NVDA", "company")[0]
    b = parse_yahoo_news(old, "NVDA", "company")[0]
    assert (a["source"], a["impact"], a["tickers"]) == ("Reuters", "positive", ["NVDA"])
    assert (b["source"], b["impact"], b["time"]) == ("Bloomberg", "negative", "2026-11-16T09:00:00Z")
    assert parse_yahoo_news([{"content": None}, "junk", {}], "X", "company") == []


def test_merge_dedupes_sorts_and_drops_old_items():
    mk = lambda t, when, tick: parse_yahoo_news([{"title": t, "link": "https://y/" + t, "publisher": "P", "providerPublishTime": int(when.timestamp())}], tick, "company")[0]
    items = [
        mk("Chip stocks rally", datetime(2026, 11, 17, 9, tzinfo=timezone.utc), "NVDA"),
        mk("Chip stocks rally!", datetime(2026, 11, 17, 8, tzinfo=timezone.utc), "AMD"),  # same story
        mk("Old story", datetime(2026, 11, 1, tzinfo=timezone.utc), "AMD"),
        mk("Newer story", datetime(2026, 11, 17, 11, tzinfo=timezone.utc), "AAPL"),
    ]
    out = merge(items, limit=10, now=NOW)
    assert [i["title"] for i in out] == ["Newer story", "Chip stocks rally"]
    assert out[1]["tickers"] == ["AMD", "NVDA"]
    assert len(merge(items, limit=1, now=NOW)) == 1


def test_news_tone():
    mk = lambda t: parse_yahoo_news([{"title": t, "link": "https://y/" + t, "publisher": "P", "providerPublishTime": int(NOW.timestamp()) - 3600}], "X", "company")[0]
    assert news_tone([mk("X beats estimates"), mk("X jumps"), mk("X faces probe")], NOW) == "positive"
    assert news_tone([mk("X misses"), mk("X to hold event")], NOW) == "negative"
    assert news_tone([mk("X to hold event")], NOW) == "neutral"
    assert news_tone([], NOW) is None


import pytest  # noqa: E402


@pytest.mark.parametrize("title,expected", [
    ("Oil jumps as Iran tensions escalate", "negative"),  # rising oil hurts stocks
    ("Oil falls as ceasefire holds", "positive"),
    ("Stocks rally as inflation cools", "positive"),  # both halves good, only "cools" is flipped
    ("Stocks fall as yields surge", "negative"),
    ("Dollar slides to two-month low", "positive"),
    ("Crude oil prices jump 3%", "negative"),
])
def test_direction_words_flip_after_oil_yields_inflation_dollar(title, expected):
    assert impact_of(title) == expected

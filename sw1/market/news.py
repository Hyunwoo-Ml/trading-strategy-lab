"""
SW1 -- headlines for the Fin-tip_lab main page and per-ticker news tone.

Sources are free: official RSS feeds (Federal Reserve, BLS, SEC) and the
headlines Yahoo Finance lists for a ticker. Only the title, source, time and
link are kept -- never article text.

Tags come from keyword rules, not a language model, so they are cheap and
predictable but blunt:
  topics      -- 연준, 물가, 고용, 유가, 미중, 관세, 지정학, 실적, AI, 반도체, ...
  impact      -- positive / negative / neutral for stocks, from wording
                 ("beats", "surges" vs "misses", "plunges", "sanctions")
  importance  -- 1..3: official sources and Fed/inflation/jobs news rank higher
"""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

FEEDS = (
    # (source label, url, base importance)
    ("연준", "https://www.federalreserve.gov/feeds/press_all.xml", 2),
    ("미 노동통계국", "https://www.bls.gov/feed/bls_latest.rss", 2),
    ("SEC", "https://www.sec.gov/news/pressreleases.rss", 1),
)

# Yahoo headline lists that cover the market as a whole.
MACRO_NEWS_TICKERS = ("SPY", "QQQ", "^TNX", "CL=F", "GC=F", "DX-Y.NYB")

# Yahoo mixes personal-finance and stock-pick pieces into ticker lists; they
# are not news. Wire services get one extra importance star.
SKIP_SOURCES = {"Motley Fool", "Moneywise", "Yahoo Personal Finance", "24/7 Wall St.", "GuruFocus.com",
                "Simply Wall St.", "Zacks", "Insider Monkey", "Yahoo Finance Video"}
WIRE_SOURCES = {"Reuters", "Bloomberg", "Associated Press", "AP Finance", "The Wall Street Journal",
                "Financial Times", "CNBC", "Barrons.com", "MarketWatch"}

# Words a headline must contain to count as being about a company, beyond its ticker.
COMPANY_ALIASES = {
    "AAPL": ("apple", "iphone"), "MSFT": ("microsoft", "azure"), "NVDA": ("nvidia",), "GOOGL": ("google", "alphabet", "waymo"),
    "AMZN": ("amazon", "aws"), "META": ("meta", "facebook", "instagram", "zuckerberg"), "TSLA": ("tesla", "musk"),
    "AVGO": ("broadcom",), "TSM": ("tsmc", "taiwan semiconductor"), "PLTR": ("palantir",),
}

TOPICS: dict[str, tuple[str, ...]] = {
    "연준": (r"\bfed\b", r"\bfomc\b", r"powell", r"federal reserve", r"rate cuts?\b", r"rate hikes?\b", r"interest rates?"),
    "물가": (r"inflation", r"\bcpi\b", r"\bpce\b", r"\bppi\b", r"consumer prices", r"producer prices"),
    "고용": (r"payrolls?", r"\bjobs?\b", r"unemployment", r"jobless", r"labor market", r"employment"),
    "성장": (r"\bgdp\b", r"recession", r"retail sales", r"\bism\b", r"manufacturing", r"consumer spending"),
    "유가": (r"\boil\b", r"crude", r"\bopec", r"brent", r"\bwti\b", r"gasoline"),
    "미중": (r"\bchina\b", r"chinese", r"beijing", r"\bxi\b"),
    "관세": (r"tariffs?", r"trade war", r"export controls?", r"trade deal"),
    "지정학": (r"\biran", r"israel", r"russia", r"ukraine", r"\bwar\b", r"missile", r"sanctions?", r"taiwan", r"middle east", r"gaza"),
    "실적": (r"earnings", r"revenue", r"quarterly", r"guidance", r"\beps\b", r"profit", r"results"),
    "AI": (r"\bai\b", r"artificial intelligence", r"data cent(er|re)s?", r"\bgpus?\b", r"\bllm"),
    "반도체": (r"\bchips?\b", r"semiconductor", r"chipmakers?", r"foundry", r"\bhbm\b"),
    "채권": (r"treasur(y|ies)", r"yields?\b", r"\bbonds?\b"),
    "달러": (r"\bdollar\b", r"currenc(y|ies)", r"\byen\b", r"\beuro\b"),
    "크립토": (r"bitcoin", r"crypto", r"ether(eum)?\b", r"stablecoin"),
    "규제": (r"antitrust", r"regulat", r"lawsuit", r"\bprobe\b", r"\bftc\b", r"\bdoj\b", r"investigation", r"enforcement"),
    "정부": (r"white house", r"congress", r"senate", r"shutdown", r"treasury secretary", r"president"),
}

# Words about price direction. For most headlines "up" is good for stocks;
# when the thing moving is oil, yields, inflation or the dollar, it's the
# reverse ("Oil jumps", "Yields climb" weigh on stocks), so they get flipped.
UP_MOVES = (
    r"\bsurges?\b", r"\bsoars?\b", r"\bjumps?\b", r"\brall(y|ies)\b", r"record high", r"\brebounds?\b",
    r"\bgains?\b", r"\bclimbs?\b", r"\brises?\b", r"\bspikes?\b", r"\bhotter\b",
)
DOWN_MOVES = (
    r"\bplunges?\b", r"\btumbles?\b", r"\bslumps?\b", r"\bsinks?\b", r"\bfalls?\b", r"\bdrops?\b", r"\bslides?\b",
    r"\bcools?\b", r"\beases?\b", r"\bcrash",
)
POSITIVE = (
    r"\bbeats?\b", r"\btops\b", r"raises? (its |full-year )?(guidance|outlook|forecast)", r"\bupgrades?d?\b",
    r"\bstrong(er)?\b", r"\bapprov(es|al|ed)\b", r"\bbuybacks?\b", r"rate cuts?\b", r"\bdeal\b", r"\bpartnership\b",
    r"\bbullish\b", r"\boutperform", r"\bceasefire\b",
)
NEGATIVE = (
    r"\bmiss(es)?\b", r"\bdowngrades?d?\b", r"cuts? (its )?(guidance|outlook|forecast)", r"\bweak(er|ens)?\b",
    r"recession", r"layoffs?", r"\bsanctions?\b", r"\bprobe\b", r"lawsuit", r"\bwar\b", r"sell-?off", r"\bwarns?\b",
    r"\bwarning\b", r"\bdelays?\b", r"\bhalts?\b", r"\bbans?\b", r"tariffs?", r"\bbearish\b", r"\bfears?\b",
    r"rate hikes?\b", r"\bshutdown\b", r"tensions?", r"escalat",
)
# Subjects whose rise is bad news for stocks. A direction word within three
# words after one of them is read the other way round.
INVERSE_SUBJECTS = r"\b(oil|crude|brent|wti|yields?|treasur(?:y|ies)|inflation|cpi|prices|dollar|vix|volatility)\b"
KEY_MACRO_TOPICS = {"연준", "물가", "고용"}

_TOPIC_RE = {k: re.compile("|".join(v), re.I) for k, v in TOPICS.items()}
_POS_RE = [re.compile(p, re.I) for p in POSITIVE]
_NEG_RE = [re.compile(p, re.I) for p in NEGATIVE]
_UP_RE = [re.compile(p, re.I) for p in UP_MOVES]
_DOWN_RE = [re.compile(p, re.I) for p in DOWN_MOVES]
_AFTER_SUBJECT = r"(?:\W+\w+){0,3}?\W+"
_INV_UP_RE = [re.compile(INVERSE_SUBJECTS + _AFTER_SUBJECT + "(?:" + p + ")", re.I) for p in UP_MOVES]
_INV_DOWN_RE = [re.compile(INVERSE_SUBJECTS + _AFTER_SUBJECT + "(?:" + p + ")", re.I) for p in DOWN_MOVES]


def topics_of(title: str) -> list[str]:
    return [k for k, rx in _TOPIC_RE.items() if rx.search(title)]


def impact_of(title: str) -> str:
    up = sum(1 for rx in _UP_RE if rx.search(title))
    down = sum(1 for rx in _DOWN_RE if rx.search(title))
    inv_up = sum(1 for rx in _INV_UP_RE if rx.search(title))  # "oil jumps": a rise that hurts stocks
    inv_down = sum(1 for rx in _INV_DOWN_RE if rx.search(title))  # "inflation cools": a fall that helps
    up, down = up - inv_up + inv_down, down - inv_down + inv_up
    pos = sum(1 for rx in _POS_RE if rx.search(title)) + up
    neg = sum(1 for rx in _NEG_RE if rx.search(title)) + down
    return "positive" if pos > neg else "negative" if neg > pos else "neutral"


def importance_of(base: int, topics: list[str]) -> int:
    return max(1, min(3, base + (1 if KEY_MACRO_TOPICS & set(topics) else 0)))


def _item(source: str, title: str, link: str, when: datetime, base: int, kind: str, tickers=()) -> dict:
    title = re.sub(r"\s+", " ", title).strip()
    t = topics_of(title)
    return {
        "id": hashlib.sha1((title.lower() + source).encode()).hexdigest()[:12],
        "time": when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": source, "title": title, "link": link, "kind": kind,
        "tickers": list(tickers), "topics": t, "impact": impact_of(title), "importance": importance_of(base, t),
    }


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        dt = parsedate_to_datetime(text)  # RSS pubDate
    except (TypeError, ValueError):
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))  # Atom / ISO
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_rss(xml_text: str, source: str, base: int) -> list[dict]:
    """RSS 2.0 or Atom -> items. Malformed entries are skipped."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    atom = "{http://www.w3.org/2005/Atom}"
    for node in root.iter():
        tag = node.tag.split("}")[-1]
        if tag not in ("item", "entry"):
            continue
        title = node.findtext("title") or node.findtext(f"{atom}title")
        link = node.findtext("link")
        if not link:
            el = node.find(f"{atom}link")
            link = el.get("href") if el is not None else None
        when = _parse_time(node.findtext("pubDate") or node.findtext(f"{atom}updated") or node.findtext(f"{atom}published"))
        if title and link and when:
            out.append(_item(source, title, link.strip(), when, base, "macro"))
    return out


def parse_yahoo_news(raw: list, ticker: str | None, kind: str) -> list[dict]:
    """yfinance Ticker.news, old flat format or the newer {'content': {...}} one."""
    out = []
    for n in raw or []:
        c = n.get("content") if isinstance(n, dict) and isinstance(n.get("content"), dict) else n
        if not isinstance(c, dict):
            continue
        title = c.get("title")
        link = ((c.get("canonicalUrl") or {}).get("url") or (c.get("clickThroughUrl") or {}).get("url") or c.get("link"))
        source = (c.get("provider") or {}).get("displayName") or c.get("publisher") or "Yahoo Finance"
        when = _parse_time(c.get("pubDate") or c.get("displayTime"))
        if when is None and c.get("providerPublishTime"):
            when = datetime.fromtimestamp(int(c["providerPublishTime"]), tz=timezone.utc)
        if title and link and when:
            out.append(_item(source, title, link, when, 2 if source in WIRE_SOURCES else 1, kind, [ticker] if ticker else []))
    return out


def mentions(title: str, ticker: str, name: str | None = None) -> bool:
    """Whether a headline is about this company: its ticker as a word, an
    alias, or the first word of its name ("NVIDIA", "Meta Platforms" -> meta)."""
    if re.search(rf"(?<![A-Za-z]){re.escape(ticker)}(?![A-Za-z])", title):
        return True
    words = list(COMPANY_ALIASES.get(ticker, ()))
    if name:
        first = re.split(r"[\s,.]+", name.strip())[0]
        if len(first) >= 3:
            words.append(first)
    low = title.lower()
    return any(re.search(rf"\b{re.escape(w.lower())}\b", low) for w in words)


def keep_for_ticker(items: list[dict], ticker: str, name: str | None = None) -> list[dict]:
    """A ticker's Yahoo list minus stock-pick pieces and stories that don't name it."""
    return [i for i in items if i["source"] not in SKIP_SOURCES and mentions(i["title"], ticker, name)]


def keep_for_macro(items: list[dict]) -> list[dict]:
    """Market-wide lists keep only items with a recognisable macro topic."""
    return [i for i in items if i["source"] not in SKIP_SOURCES and i["topics"]]


def merge(items: list[dict], limit: int, now: datetime, max_age_days: int = 4) -> list[dict]:
    """Newest first, one copy of each headline, nothing older than max_age_days."""
    cutoff = now - timedelta(days=max_age_days)
    seen, out = {}, []
    for it in sorted(items, key=lambda x: x["time"], reverse=True):
        if datetime.fromisoformat(it["time"].replace("Z", "+00:00")) < cutoff:
            continue
        key = re.sub(r"[^a-z0-9]", "", it["title"].lower())[:80]
        if key in seen:
            prev = seen[key]  # same story from several ticker lists: keep one, union the tickers
            prev["tickers"] = sorted(set(prev["tickers"]) | set(it["tickers"]))
            continue
        seen[key] = it
        out.append(it)
    return out[:limit]


def news_tone(items: list[dict], now: datetime, days: int = 3) -> str | None:
    """A ticker's recent headline tone: positive / negative / neutral, or None
    when it had no headlines in the window."""
    cutoff = now - timedelta(days=days)
    recent = [i for i in items if datetime.fromisoformat(i["time"].replace("Z", "+00:00")) >= cutoff]
    if not recent:
        return None
    net = sum(1 if i["impact"] == "positive" else -1 if i["impact"] == "negative" else 0 for i in recent)
    return "positive" if net > 0 else "negative" if net < 0 else "neutral"

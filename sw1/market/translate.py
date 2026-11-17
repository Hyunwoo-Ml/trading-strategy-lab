"""
SW1 -- Korean versions of the English headlines, with a free offline model.

Meta's NLLB-200 (distilled 600M, int8 CTranslate2 build) runs on the GitHub
Actions runner itself: no API key, no per-call cost, no daily limit. It is a
general-purpose model, so a small finance glossary is applied before and after
translation to fix the terms it reliably gets wrong ("Release Schedule" ->
"석방 일정", "chip stocks" -> "칩 주식").

Each headline is translated once: callers pass the translations from their
previous output as a cache, and only new titles reach the model. If the model
can't be loaded (no network, no package), items simply keep their English
title and the page shows that instead.
"""
from __future__ import annotations

import re
from typing import Callable, Iterable

MODEL_REPO = "JustFrederik/nllb-200-distilled-600M-ct2-int8"
TOKENIZER_REPO = "facebook/nllb-200-distilled-600M"
BATCH = 32

# Bump when the rules below change: cached translations made under an older
# version are ignored, so every headline is redone once with the new rules.
VERSION = 4

LABEL = r"(Morning Bid|Market Chatter|Stock Market Today|Breakingviews|Exclusive|BREAKING|Update(?: \d+)?)"

# Applied to the English title before translation.
PRE = (
    (r"[‘’]", "'"), (r"[“”]", '"'), (r"\s*[—–]\s*", ", "),
    (r"(?i)^(" + LABEL + r"\s*[:\-]\s*)+", ""),
    (r"\s*:\s*(AlphaCheck|Chart of the Day)\s*$", ""),
    (r"\bPre-Bell\b", "before the opening bell"),
    (r"\bAfter-Hours\b", "after-hours trading"),
    (r"\bbps\b", "basis points"),
    (r"(?i)\b(edges?|ticks?|inch(?:es)?) (higher|up)\b", "rises slightly"),
    (r"(?i)\b(edges?|ticks?|inch(?:es)?) (lower|down)\b", "falls slightly"),
    (r"(?i)\bloonie\b", "Canadian dollar"), (r"(?i)\bgreenback\b", "US dollar"),
    (r"\b[Rr]ate [Hh]ikes?\b", "interest rate increase"), (r"\b[Rr]ate [Cc]uts?\b", "interest rate cut"),
    (r"\b(Equity|Stock|Index) Futures\b", "stock index futures"),
    (r"\bExchange-Traded Funds\b", "ETFs"),
    (r"\s*\((?:NYSE|NASDAQ|Nasdaq|NYSEARCA)?:?\s*[A-Z]{1,5}\)", ""),  # "(NOW)" read as the word "now"
    (r"(?i)\bsell-?offs?\b", "sharp drop"),
    (r"(?i)\bsells? off\b", "falls sharply"),
    (r"(?i)\bbuy the dip\b", "buy on the decline"),
    (r"(?i)\boutages?\b", "service disruption"),
    # "Netflix Stock Down 47%": "stock"/"shares" here is the share price, not market share
    (r"\b(?:[Ss]tock|[Ss]hares)\b(?='s|\s+(?i:down|up|fell|falls|rises|rose|sinks?|sank|jumps?|jumped|climbs?|slides?|slid|drops?|dropped|soars?|soared|plunges?|plunged|tumbles?|gains?|rall(?:y|ies)|surges?|slumps?|is down|is up))",
     "share price"),
    (r"\bearnings\b(?! (report|results|call|season))", "earnings report"),
)
# Applied to the Korean output, in order (longer phrases first).
POST = (
    ("⁇", ""),
    ("경제 석방", "경제지표 발표"), ("석방 일정", "발표 일정"), ("석방", "발표"),
    ("칩 주식", "반도체주"), ("칩 제조업체", "반도체 기업"), ("칩메이커", "반도체 기업"), ("칩 파손", "반도체 급락"),
    ("칩", "반도체"),
    ("탱커", "유조선"), ("탱크 공격", "유조선 공격"),
    ("연방 준비 제도", "연준"), ("연방준비제도", "연준"), ("월 스트리트", "월가"),
    ("재무부 수익률", "국채 금리"), ("재무 수익률", "국채 금리"), ("국채 수익률", "국채 금리"),
    ("주식 미래에셋", "주가지수 선물"), ("주가 지수 선물", "주가지수 선물"), ("주식 선물", "주가지수 선물"),
    ("거래소에서 거래되는 자금", "ETF"), ("이론 머스크", "일론 머스크"), ("주식 지수 선물", "주가지수 선물"),
    ("엔트로픽", "앤트로픽"), ("인트로피크", "앤트로픽"), ("주식 가격", "주가"),
    ("기획재정 수익률", "국채 금리"), ("재무부 금리", "국채 금리"), ("석유 미래", "원유 선물"), ("원유 미래", "원유 선물"),
    ("골드 퓨처스", "금 선물"), ("금 미래", "금 선물"), ("작은 캡", "소형주"), ("영업 통화", "실적 발표 콜"),
    ("스톡", "주식"), ("미치광이", "캐나다 달러"),
    ("주식 시장", "증시"), ("수익 보고서", "실적 발표"), ("수익을 앞두고", "실적 발표를 앞두고"),
)
# Company names: the model translates them word by word ("Applied Materials"
# -> "사용 된 재료"). The Korean name is put into the English title before
# translation, which the model keeps. If it still mangles one (rare
# syllables), the title is retried with the ticker in its place and the ticker
# swapped for the Korean name afterwards -- not for tickers that are also
# English words (NOW, BE, MS...), which the model would translate.
# (English names, longest first) -> (Korean name, ticker or None)
NAMES = (
    ("Applied Optoelectronics", "어플라이드 옵토일렉트로닉스", "AAOI"), ("Applied Materials", "어플라이드 머티리얼즈", "AMAT"),
    ("Palo Alto Networks", "팔로알토 네트웍스", "PANW"), ("Constellation Energy", "컨스텔레이션 에너지", "CEG"),
    ("Bank of America", "뱅크오브아메리카", "BAC"), ("JPMorgan Chase", "JP모건", "JPM"), ("Berkshire Hathaway", "버크셔 해서웨이", "BRKB"),
    ("ON Semiconductor", "온세미", None), ("Arista Networks", "아리스타", "ANET"), ("Morgan Stanley", "모건스탠리", None),
    ("Goldman Sachs", "골드만삭스", None), ("Meta Platforms", "메타", "META"), ("Arm Holdings", "암홀딩스", None),
    ("Bloom Energy", "블룸 에너지", None), ("Eos Energy", "이오스 에너지", "EOSE"), ("Lam Research", "램리서치", "LRCX"),
    ("Rocket Lab", "로켓랩", "RKLB"), ("Super Micro", "슈퍼마이크로", "SMCI"), ("Supermicro", "슈퍼마이크로", "SMCI"),
    ("UnitedHealth", "유나이티드헬스", "UNH"), ("Wells Fargo", "웰스파고", "WFC"), ("Home Depot", "홈디포", None),
    ("GE Vernova", "GE 버노바", "GEV"), ("Tempus AI", "템퍼스 AI", None), ("Eli Lilly", "일라이릴리", "LLY"),
    ("JPMorgan", "JP모건", "JPM"), ("JP Morgan", "JP모건", "JPM"), ("Palo Alto", "팔로알토", "PANW"),
    ("ServiceNow", "서비스나우", None), ("CrowdStrike", "크라우드스트라이크", "CRWD"), ("Salesforce", "세일즈포스", "CRM"),
    ("CoreWeave", "코어위브", "CRWV"), ("Robinhood", "로빈후드", "HOOD"), ("Coinbase", "코인베이스", None),
    ("AppLovin", "앱러빈", None), ("Anthropic", "앤트로픽", "ANTHR"), ("OpenAI", "오픈AI", "OPNAI"),
    ("SpaceX", "스페이스X", "SPACX"), ("Microsoft", "마이크로소프트", "MSFT"), ("Alphabet", "알파벳", "GOOGL"),
    ("Google", "구글", "GOOGL"), ("Amazon", "아마존", "AMZN"), ("Nvidia", "엔비디아", "NVDA"), ("NVIDIA", "엔비디아", "NVDA"),
    ("Broadcom", "브로드컴", "AVGO"), ("Qualcomm", "퀄컴", "QCOM"), ("Marvell", "마벨", "MRVL"), ("Micron", "마이크론", None),
    ("Intel", "인텔", "INTC"), ("Sandisk", "샌디스크", "SNDK"), ("SanDisk", "샌디스크", "SNDK"), ("Seagate", "시게이트", "STX"),
    ("Oracle", "오라클", "ORCL"), ("Arista", "아리스타", "ANET"), ("Nebius", "네비우스", "NBIS"), ("Lumentum", "루멘텀", None),
    ("Coherent", "코히런트", "COHR"), ("Vertiv", "버티브", "VRT"), ("Vistra", "비스트라", "VST"), ("Oklo", "오클로", "OKLO"),
    ("Palantir", "팔란티어", "PLTR"), ("Netflix", "넷플릭스", "NFLX"), ("Walmart", "월마트", "WMT"), ("Evercore", "에버코어", "EVR"),
    ("Bernstein", "번스타인", None), ("Affirm", "어펌", "AFRM"), ("Circle", "서클", "CRCL"), ("SoFi", "소파이", "SOFI"),
    ("IonQ", "아이온큐", "IONQ"), ("Apple", "애플", "AAPL"), ("Tesla", "테슬라", "TSLA"), ("Lilly", "일라이릴리", "LLY"),
    ("BofA", "뱅크오브아메리카", "BAC"), ("Uber", "우버", "UBER"),
    ("Meta", "메타", "META"), ("Dell", "델", "DELL"),
)
_NAME_RE = [(re.compile(r"\b" + re.escape(en) + r"\b"), ko, tick) for en, ko, tick in NAMES]

# Dollar amounts are written out in Korean units before translation: the model
# got "$3.30bn" as 3조30억 and "$400 billion" as 400억 (should be 4,000억).
MONEY = re.compile(r"\$(\d+(?:\.\d+)?)\s?(bn|billion|mn|million|m|tn|trillion)\b"
                   r"|\b(\d+(?:\.\d+)?) (billion|million|trillion) dollars?\b", re.I)
SCALE = {"bn": 1e9, "billion": 1e9, "mn": 1e6, "million": 1e6, "m": 1e6, "tn": 1e12, "trillion": 1e12}


def korean_amount(dollars: float) -> str:
    """4e11 -> "4,000억 달러", 3.3e9 -> "33억 달러", 5.5e8 -> "5억 5,000만 달러"."""
    jo, rest = divmod(round(dollars), 10 ** 12)
    eok, rest = divmod(rest, 10 ** 8)
    man = round(rest / 10 ** 4)
    if jo:
        parts = [f"{jo:,}조", f"{eok:,}억" if eok else ""]
    else:
        parts = [f"{eok:,}억" if eok else "", f"{man:,}만" if man else ""]
    return " ".join(p for p in parts if p) + " 달러"


def _money(m: re.Match) -> str:
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    return korean_amount(float(num) * SCALE[unit.lower()])


Backend = Callable[[list[str]], list[str]]


def prepare(title: str, names: str = "ko") -> tuple[str, list[tuple[str, str | None]]]:
    """English title ready for the model, and the company names found in it
    as (Korean name, ticker). `names`: "ko" puts the Korean name in the text,
    "ticker" puts the ticker (when there is a usable one)."""
    out = MONEY.sub(_money, title)
    for pattern, repl in PRE:
        out = re.sub(pattern, repl, out)
    found = []
    for rx, ko, tick in _NAME_RE:
        if rx.search(out):
            found.append((ko, tick))
            out = rx.sub(tick if names == "ticker" and tick else ko, out)
    return out.strip(" ,"), found


def preprocess(title: str) -> str:
    return prepare(title)[0]


def _keeps_names(text: str, found: list[tuple[str, str | None]]) -> bool:
    flat = text.replace(" ", "")
    return all(ko.replace(" ", "") in flat for ko, _ in found)


PARTICLES = {"이": ("이", "가"), "가": ("이", "가"), "은": ("은", "는"), "는": ("은", "는"),
             "을": ("을", "를"), "를": ("을", "를"), "과": ("과", "와"), "와": ("과", "와")}


def _has_final(word: str) -> bool:
    """True when the last syllable has a final consonant (받침): 금리 -> False, 연준 -> True."""
    c = word[-1]
    return "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28 != 0


def _replace(text: str, old: str, new: str) -> str:
    """Replace `old` with `new` and fix an attached subject/topic/object particle
    so it still fits ("재무 수익률이" -> "국채 금리가")."""
    if not new:
        return text.replace(old, new)

    def fix(m: re.Match) -> str:
        p = m.group(1)
        if not p:
            return new
        return new + PARTICLES[p][0 if _has_final(new) else 1]

    # a particle only counts when a word boundary follows ("연준이사회" keeps its 이)
    return re.sub(re.escape(old) + r"(?:(이|가|은|는|을|를|과|와)(?=[\s,.!?:)]|$))?", fix, text)


def postprocess(text: str) -> str:
    out = text.strip()
    for a, b in POST:
        out = _replace(out, a, b)
    out = re.sub(r"수익(?!률)", "실적", out)  # in headlines "earnings", not "yield"
    # "chip stocks fall": share price, not market share
    out = re.sub(r"(?<!시장 )점유율(?:(이|가|은|는|을|를)(?=[\s,.!?:)]|$))?",
                 lambda m: "주가" + (PARTICLES[m.group(1)][1] if m.group(1) else ""), out)
    out = re.sub(r"(?<=[가-힣]) (은|는|을|를|의)(?=[\s,.?!]|$)", r"\1", out)  # "관리자 는" -> "관리자는"
    return re.sub(r"\s+", " ", out).strip().rstrip(".")


def nllb_backend() -> Backend | None:
    """Load NLLB lazily. Returns None when the packages or the model files
    aren't available, so callers fall back to English."""
    try:
        import glob
        import os

        import ctranslate2
        import sentencepiece as spm
        from huggingface_hub import snapshot_download

        path = snapshot_download(MODEL_REPO)
        sp_file = (glob.glob(os.path.join(path, "*.model")) or [None])[0]
        if sp_file is None:
            sp_file = os.path.join(snapshot_download(TOKENIZER_REPO, allow_patterns=["sentencepiece.bpe.model"]),
                                   "sentencepiece.bpe.model")
        sp = spm.SentencePieceProcessor(model_file=sp_file)
        model = ctranslate2.Translator(path, device="cpu", compute_type="int8")
    except Exception as exc:  # noqa: BLE001
        print(f"[translate] model unavailable, keeping English: {str(exc)[:160]}")
        return None

    def run(texts: list[str]) -> list[str]:
        out = []
        for i in range(0, len(texts), BATCH):
            chunk = texts[i:i + BATCH]
            src = [["eng_Latn"] + sp.encode(t, out_type=str) + ["</s>"] for t in chunk]
            res = model.translate_batch(src, target_prefix=[["kor_Hang"]] * len(src), beam_size=4, max_decoding_length=128)
            out += [sp.decode(r.hypotheses[0][1:]) for r in res]
        return out

    return run


class Translator:
    """Title -> Korean, with a cache. `backend` defaults to NLLB, loaded on
    first use; tests pass a fake."""

    def __init__(self, cache: dict[str, str] | None = None, backend: Backend | None = None, load_default: bool = True):
        self.cache = dict(cache or {})
        self._backend = backend
        self._load_default = load_default and backend is None
        self.translated = 0

    def _get_backend(self) -> Backend | None:
        if self._backend is None and self._load_default:
            self._load_default = False
            self._backend = nllb_backend()
        return self._backend

    def translate(self, titles: Iterable[str]) -> dict[str, str]:
        titles = [t for t in dict.fromkeys(titles) if t]
        todo = [t for t in titles if t not in self.cache]
        if todo:
            backend = self._get_backend()
            if backend is not None:
                try:
                    first = [prepare(t, "ko") for t in todo]
                    out = {t: postprocess(k) for t, k in zip(todo, backend([p for p, _ in first]))}
                    # names the model mangled: retry with tickers, then put the Korean names back
                    retry = [t for t, (_, found) in zip(todo, first)
                             if found and not _keeps_names(out[t], found) and all(tick for _, tick in found)]
                    if retry:
                        second = [prepare(t, "ticker") for t in retry]
                        for t, (_, found), k in zip(retry, second, backend([p for p, _ in second])):
                            for ko_name, tick in sorted(found, key=lambda f: -len(f[1])):
                                k = _replace(k, tick, ko_name)
                            k = postprocess(k)
                            if _keeps_names(k, found):
                                out[t] = k
                    for t in todo:
                        k = out[t]
                        if k and re.search(r"[가-힣]", k):
                            self.cache[t] = k
                            self.translated += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"[translate] failed, keeping English: {str(exc)[:160]}")
        return {t: self.cache[t] for t in titles if t in self.cache}

    def annotate(self, items: list[dict]) -> list[dict]:
        """Set `title_ko` on each item that has a translation."""
        ko = self.translate(i.get("title") for i in items)
        for i in items:
            if i.get("title") in ko:
                i["title_ko"] = ko[i["title"]]
                i["ko_v"] = VERSION
        return items


def cache_from(item_lists: Iterable[Iterable[dict]]) -> dict[str, str]:
    """Translations already present in earlier output (title -> title_ko)."""
    out = {}
    for items in item_lists:
        for i in items or []:
            if isinstance(i, dict) and i.get("title") and i.get("title_ko") and i.get("ko_v") == VERSION:
                out[i["title"]] = i["title_ko"]
    return out

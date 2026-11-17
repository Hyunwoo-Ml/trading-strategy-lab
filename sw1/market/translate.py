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
VERSION = 2

LABEL = r"(Morning Bid|Market Chatter|Stock Market Today|Breakingviews|Exclusive|BREAKING|Update(?: \d+)?)"

# Applied to the English title before translation.
PRE = (
    (r"[‘’]", "'"), (r"[“”]", '"'), (r"\s*[—–]\s*", ", "),
    (r"(?i)^(" + LABEL + r"\s*[:\-]\s*)+", ""),
    (r"\s*:\s*(AlphaCheck|Chart of the Day)\s*$", ""),
    (r"\bPre-Bell\b", "before the opening bell"),
    (r"\bAfter-Hours\b", "after-hours trading"),
    (r"\bbps\b", "basis points"),
    (r"\b(edges|ticks|inches) (higher|up)\b", "rises slightly"),
    (r"\b(edges|ticks|inches) (lower|down)\b", "falls slightly"),
    (r"\b(Equity|Stock|Index) Futures\b", "stock index futures"),
    (r"\bExchange-Traded Funds\b", "ETFs"),
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
    ("거래소에서 거래되는 자금", "ETF"), ("이론 머스크", "일론 머스크"),
    ("주식 시장", "증시"), ("수익 보고서", "실적 발표"), ("수익을 앞두고", "실적 발표를 앞두고"),
)
MONEY = re.compile(r"\$(\d+(?:\.\d+)?)\s?(bn|billion|mn|million|m|tn|trillion)\b", re.I)
MONEY_WORD = {"bn": "billion", "billion": "billion", "mn": "million", "million": "million", "m": "million",
              "tn": "trillion", "trillion": "trillion"}


def _money(m: re.Match) -> str:
    """$3.30bn -> $3.3 billion (the model read "3.30bn" as 3 trillion 30 billion)."""
    num = m.group(1).rstrip("0").rstrip(".") if "." in m.group(1) else m.group(1)
    return f"${num} {MONEY_WORD[m.group(2).lower()]}"


Backend = Callable[[list[str]], list[str]]


def preprocess(title: str) -> str:
    out = MONEY.sub(_money, title)
    for pattern, repl in PRE:
        out = re.sub(pattern, repl, out)
    return out.strip(" ,")


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
                    ko = backend([preprocess(t) for t in todo])
                    for t, k in zip(todo, ko):
                        k = postprocess(k)
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

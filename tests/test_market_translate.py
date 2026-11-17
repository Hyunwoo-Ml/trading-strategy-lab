"""Headline translation (sw1/market/translate.py) with a fake backend --
the real NLLB model only runs in the market workflows."""
from sw1.market.translate import Translator, cache_from, postprocess, preprocess


class Fake:
    def __init__(self, out=None):
        self.calls = []
        self.out = out

    def __call__(self, texts):
        self.calls.append(list(texts))
        return self.out if self.out is not None else [f"번역: {t}" for t in texts]


def test_preprocess_strips_column_labels_and_expands_jargon():
    assert preprocess("Morning Bid: Chip wreck") == "Chip wreck"
    assert preprocess("Nvidia Dims Wall Street Pre-Bell") == "Nvidia Dims Wall Street before the opening bell"
    assert preprocess("Fed cuts 25 bps") == "Fed cuts 25 basis points"


def test_postprocess_fixes_common_mistranslations():
    assert postprocess("미국 달러는 경제 석방 일정 앞두고 하락.") == "미국 달러는 경제지표 발표 일정 앞두고 하락"
    assert postprocess("칩 주식  베팅") == "반도체주 베팅"
    assert postprocess("연방 준비 제도 의장") == "연준 의장"


def test_only_uncached_titles_reach_the_backend():
    fake = Fake()
    tr = Translator(cache={"Old": "옛 제목"}, backend=fake)
    got = tr.translate(["Old", "New", "New", ""])
    assert got == {"Old": "옛 제목", "New": "번역: New"}
    assert fake.calls == [["New"]]
    assert tr.translated == 1
    tr.translate(["New"])
    assert len(fake.calls) == 1  # now cached


def test_annotate_sets_title_ko_and_keeps_english():
    items = [{"title": "Oil jumps"}, {"title": "Stocks fall"}]
    Translator(backend=Fake()).annotate(items)
    assert items[0] == {"title": "Oil jumps", "title_ko": "번역: Oil jumps"}
    assert items[1]["title_ko"] == "번역: Stocks fall"


def test_output_without_hangul_is_discarded():
    items = [{"title": "Nvidia"}]
    Translator(backend=Fake(out=["Nvidia"])).annotate(items)
    assert "title_ko" not in items[0]


def test_missing_or_failing_backend_keeps_english():
    def boom(texts):
        raise RuntimeError("no model")
    items = [{"title": "A"}]
    Translator(backend=boom).annotate(items)
    assert "title_ko" not in items[0]
    Translator(load_default=False).annotate(items)
    assert "title_ko" not in items[0]


def test_cache_from_previous_output():
    prev = [[{"title": "A", "title_ko": "가"}, {"title": "B"}], None, [{"title": "C", "title_ko": "다"}, "junk"]]
    assert cache_from(prev) == {"A": "가", "C": "다"}

"""Headline translation (sw1/market/translate.py) with a fake backend --
the real NLLB model only runs in the market workflows."""
from sw1.market.translate import VERSION, Translator, cache_from, postprocess, preprocess


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
    assert items[0] == {"title": "Oil jumps", "title_ko": "번역: Oil jumps", "ko_v": VERSION}
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
    prev = [[{"title": "A", "title_ko": "가", "ko_v": VERSION}, {"title": "B"}], None,
            [{"title": "C", "title_ko": "다", "ko_v": VERSION}, "junk", {"title": "D", "title_ko": "라", "ko_v": VERSION - 1}]]
    assert cache_from(prev) == {"A": "가", "C": "다"}  # D was made under older rules: redo it


def test_preprocess_cleans_real_headlines():
    assert preprocess("Tesla commits $3.30bn to expand German gigafactory") == "Tesla commits $3.3 billion to expand German gigafactory"
    assert preprocess("Lilly agrees to acquire Calytrix Bio for $1.5bn") == "Lilly agrees to acquire Calytrix Bio for $1.5 billion"
    assert preprocess("Update: Market Chatter: Amazon Lobbying Fails") == "Amazon Lobbying Fails"
    assert preprocess("Exclusive-Trump's China data center curb advances") == "Trump's China data center curb advances"
    assert preprocess("Stocks slip ahead of Nvidia earnings, Home Depot shares slide: AlphaCheck") == \
        "Stocks slip ahead of Nvidia earnings report, Home Depot shares slide"
    assert preprocess("Nvidia earnings report due") == "Nvidia earnings report due"
    assert preprocess("It’s not just AI — the dollar ‘wrecking ball’") == "It's not just AI, the dollar 'wrecking ball'"
    assert preprocess("Oil edges higher on tanker attacks") == "Oil rises slightly on tanker attacks"
    assert preprocess("Equity Futures Mixed Pre-Bell") == "stock index futures Mixed before the opening bell"


def test_postprocess_fixes_real_model_output():
    assert postprocess("재무 수익률이 약한 주택 지표 가운데 하락") == "국채 금리가 약한 주택 지표 가운데 하락"
    assert postprocess("거래소에서 거래되는 자금, 주식 미래에셋 혼조") == "ETF, 주가지수 선물 혼조"
    assert postprocess("미국 반도체 제한에 따라 칩 주가가 떨어집니다") == "미국 반도체 제한에 따라 반도체 주가가 떨어집니다"
    assert postprocess("Nvidia 수익을 앞두고 주가 하락, 국채 수익률 상승") == "Nvidia 실적 발표를 앞두고 주가 하락, 국채 금리 상승"
    assert postprocess("그것은 단지 인공지능이 아닙니다 ⁇ 달러") == "그것은 단지 인공지능이 아닙니다 달러"
    assert postprocess("이론 머스크는 말했다") == "일론 머스크는 말했다"


def test_particles_follow_the_replaced_word():
    assert postprocess("연방준비제도가 금리를 올렸다") == "연준이 금리를 올렸다"
    assert postprocess("칩 주식은 하락") == "반도체주는 하락"
    assert postprocess("탱커를 공격") == "유조선을 공격"
    assert postprocess("연방준비제도이사회 의장") == "연준이사회 의장"  # not a particle

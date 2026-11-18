"""Headline translation (sw1/market/translate.py) with a fake backend --
the real NLLB model only runs in the market workflows."""
from sw1.market.translate import VERSION, source_fixes, amounts_in, fix_amounts, korean_amount, Translator, _replace, cache_from, postprocess, prepare, preprocess


class Fake:
    def __init__(self, out=None):
        self.calls = []
        self.out = out

    def __call__(self, texts):
        self.calls.append(list(texts))
        return self.out if self.out is not None else [f"번역: {t}" for t in texts]


def test_preprocess_strips_column_labels_and_expands_jargon():
    assert preprocess("Morning Bid: Chip wreck") == "Chip wreck"
    assert preprocess("Nvidia Dims Wall Street Pre-Bell") == "엔비디아 Dims Wall Street before the opening bell"
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
    assert preprocess("Tesla commits $3.30bn to expand German gigafactory") == "테슬라 commits $3.3 billion to expand German gigafactory"
    assert preprocess("Kazakhstan unveils $85M push") == "Kazakhstan unveils $85 million push"
    assert preprocess("Update: Market Chatter: Amazon Lobbying Fails") == "아마존 Lobbying Fails"
    assert preprocess("Exclusive-Trump's China data center curb advances") == "Trump's China data center curb advances"
    assert preprocess("Stocks slip ahead of Nvidia earnings, Home Depot shares slide: AlphaCheck") == \
        "Stocks slip ahead of 엔비디아 earnings report, 홈디포 share price slide"
    assert preprocess("Nvidia earnings report due") == "엔비디아 earnings report due"
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


def test_company_names_and_market_slang():
    assert preprocess("Why Is Applied Materials Stock Down Over 5%?") == "Why Is 어플라이드 머티리얼즈 share price Down Over 5%?"
    assert preprocess("ServiceNow Stock's (NOW) Historic Sell-Off May Have Gone Too Far") == \
        "서비스나우 share price's Historic sharp drop May Have Gone Too Far"
    assert preprocess("Salesforce Sells Off Before Anthropic's IPO") == "세일즈포스 falls sharply Before 앤트로픽's IPO"
    assert preprocess("Signal: Buy the Dip on Bloom Energy Stock") == "Signal: buy on the decline on 블룸 에너지 Stock"
    assert preprocess("Apple outage today") == "애플 service disruption today"
    assert preprocess("Applied Optoelectronics falls 8% on stock offering plan") == \
        "어플라이드 옵토일렉트로닉스 falls 8% on stock offering plan"  # "stock offering" stays
    text, found = prepare("Dear Palo Alto Networks Stock Fans", "ticker")
    assert text == "Dear PANW Stock Fans" and found == [("팔로알토 네트웍스", "PANW")]
    assert prepare("ServiceNow slides", "ticker")[0] == "서비스나우 slides"  # NOW is an English word: no ticker


def test_mangled_names_are_retried_with_tickers():
    calls = []

    def model(texts):
        calls.append(list(texts))
        out = []
        for t in texts:
            if "팔로알토 네트웍스" in t:      # the model drops a rare syllable
                out.append("귀여운 팔로알토네 ⁇ 스 주가 팬")
            elif "PANW" in t:
                out.append("귀여운 PANW 주가 팬들")
            elif "CRM" in t:
                out.append("ANTHR의 IPO 전에 CRM가 급격히 떨어집니다")
            else:
                out.append("세일즈포스는 엔트로픽의 IPO 전에 급격히 하락했습니다")
        return out

    tr = Translator(backend=model)
    got = tr.translate(["Dear Palo Alto Networks Stock Fans", "Salesforce Sells Off Before Anthropic's IPO"])
    assert got["Dear Palo Alto Networks Stock Fans"] == "귀여운 팔로알토 네트웍스 주가 팬들"
    assert got["Salesforce Sells Off Before Anthropic's IPO"] == "세일즈포스는 앤트로픽의 IPO 전에 급격히 하락했습니다"
    assert len(calls) == 2 and calls[1] == ["Dear PANW Stock Fans"]  # only the mangled one is retried
    assert _replace("ANTHR의 IPO 전에 CRM가 하락", "CRM", "세일즈포스") == "ANTHR의 IPO 전에 세일즈포스가 하락"


def test_dollar_amounts_in_korean_units():
    assert korean_amount(4e11) == "4,000억 달러"
    assert korean_amount(3.3e9) == "33억 달러"
    assert korean_amount(5.5e8) == "5억 5,000만 달러"
    assert korean_amount(1.2e12) == "1조 2,000억 달러"
    assert korean_amount(8.5e7) == "8,500만 달러"
    assert preprocess("Amazon and Google Could Own $400 Billion of Anthropic Stock") == \
        "아마존 and 구글 Could Own $400 billion of 앤트로픽 Stock"
    assert preprocess('Trump Demands a "350 Billion Dollar" Military Bill') == 'Trump Demands a "$350 billion" Military Bill'


def test_more_market_slang():
    assert preprocess("The Greenback Weakens Slightly Against Canada's Loonie") == \
        "The US dollar Weakens Slightly Against Canada's Canadian dollar"
    assert preprocess("Gold Futures Inch Higher As U.S. Yields Slip") == "Gold Futures rises slightly As U.S. Yields Slip"
    assert preprocess("Will Gold Rise After the Fed's Expected Rate Hike?") == \
        "Will Gold Rise After the Fed's Expected interest rate increase?"
    assert postprocess("기획재정 수익률이 하락함에 따라") == "국채 금리가 하락함에 따라"
    assert postprocess("중동 위험에 석유 미래가 증가") == "중동 위험에 원유 선물이 증가"
    assert postprocess("미국 반도체 제한에 따라 반도체 점유율이 하락") == "미국 반도체 제한에 따라 반도체 주가가 하락"
    assert postprocess("시장 점유율이 늘었다") == "시장 점유율이 늘었다"
    assert postprocess("기금 관리자 는 테슬라 주식 을 사지 않음 의 이유") == "기금 관리자는 테슬라 주식을 사지 않음의 이유"
    assert postprocess("팔란티어 스톡이 두배가 될까") == "팔란티어 주식이 두배가 될까"


def test_model_amounts_are_corrected():
    t = "Amazon and Google Could Own $400 Billion of Anthropic Stock"
    assert amounts_in(t) == [("4,000억 달러", "4")]
    assert fix_amounts("아마존과 구글은 40억 달러의 앤트로픽 주식을 소유할 수 있습니다", amounts_in(t)) == \
        "아마존과 구글은 4,000억 달러의 앤트로픽 주식을 소유할 수 있습니다"
    t2 = 'Trump Demands a "350 Billion Dollar" Military Bill'
    assert fix_amounts('트럼프는 "3천50억 달러"의 군사 법안을 요구합니다', amounts_in(t2)) == '트럼프는 "3,500억 달러"의 군사 법안을 요구합니다'
    t3 = "IREN Tripled Revenue to $200 Million on $1.5bn Deal"
    assert fix_amounts("IREN은 15억 달러 거래로 매출을 2억 달러로 늘렸다", amounts_in(t3)) == \
        "IREN은 15억 달러 거래로 매출을 2억 달러로 늘렸다"  # matched by digits, not by order
    assert fix_amounts("15억 달러와 20억 달러", amounts_in(t3)) == "15억 달러와 2억 달러"
    assert fix_amounts("7억 달러와 9억 달러", amounts_in(t3)) == "7억 달러와 9억 달러"  # no match: left alone
    assert fix_amounts("금액이 빠진 문장", amounts_in(t)) == "금액이 빠진 문장"  # model dropped it: left alone
    assert "€" not in fix_amounts("가격은 93 달러", amounts_in("Brent near $93"))  # plain prices aren't touched


def test_futures_never_become_mirae_asset():
    assert postprocess("금 미래에셋은 약간 상승") == "금 선물은 약간 상승"


def test_repetition_loops_rebound_and_last_quarter():
    assert postprocess("판매팀을 오리엔탈 오리엔탈 오리엔탈 오리엔탈 호텔에") == "판매팀을 오리엔탈 호텔에"
    assert postprocess("매우 매우 좋다") == "매우 매우 좋다"  # a doubled word is left alone
    assert preprocess("Crypto Stocks Rebound As Risk Appetite Returns") == "Crypto Stocks recover As Risk Appetite Returns"
    assert preprocess("Oil rebounded after the drop") == "Oil recovered after the drop"
    assert preprocess("Taiwan Semiconductor Stock (TSM) Looks Too Cheap") == "TSMC Stock Looks Too Cheap"
    t = "25 Big Funds Bought Taiwan Semiconductor Last Quarter"
    assert source_fixes(t, "25개의 큰 자금이 지난 3분기 TSMC를 샀습니다") == "25개의 큰 자금이 지난 분기 TSMC를 샀습니다"
    assert source_fixes("TSMC Q3 results", "TSMC 지난 3분기 실적") == "TSMC 지난 3분기 실적"  # only when the title says "last quarter"

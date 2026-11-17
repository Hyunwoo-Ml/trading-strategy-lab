"""Temporary (Fin-tip_lab): how to keep company names intact in EN->KO headline translation."""
import glob, os, re, time
import ctranslate2, sentencepiece as spm
from huggingface_hub import snapshot_download

NAMES = [("Applied Materials", "AMAT", "어플라이드 머티리얼즈"), ("ServiceNow", "NOW", "서비스나우"),
         ("Anthropic", "ANTHR", "앤트로픽"), ("Palo Alto Networks", "PANW", "팔로알토 네트웍스"),
         ("Palo Alto", "PANW", "팔로알토"), ("Bloom Energy", "BE", "블룸 에너지"), ("Vistra", "VST", "비스트라"),
         ("Vertiv", "VRT", "버티브"), ("Bernstein", "BERN", "번스타인"), ("Lam Research", "LRCX", "램리서치"),
         ("Netflix", "NFLX", "넷플릭스"), ("Robinhood", "HOOD", "로빈후드"), ("Circle", "CRCL", "서클"),
         ("Affirm", "AFRM", "어펌"), ("Eli Lilly", "LLY", "일라이릴리"), ("Oracle", "ORCL", "오라클"),
         ("Salesforce", "CRM", "세일즈포스"), ("CrowdStrike", "CRWD", "크라우드스트라이크"),
         ("Nvidia", "NVDA", "엔비디아"), ("NVIDIA", "NVDA", "엔비디아"), ("Arista", "ANET", "아리스타"),
         ("Evercore", "EVR", "에버코어"), ("Applied Optoelectronics", "AAOI", "어플라이드 옵토일렉트로닉스"),
         ("Home Depot", "HD", "홈디포"), ("Apple", "AAPL", "애플"), ("Tesla", "TSLA", "테슬라")]
TITLES = [
    "Why Is Applied Materials Stock Down Over 5%?",
    "ServiceNow Stock's (NOW) Historic Sell-Off May Have Gone Too Far",
    "Salesforce Sells Off Before Anthropic's IPO",
    "Dear Palo Alto Networks Stock Fans, Mark Your Calendars for November 18",
    "Signal: Buy the Dip on Bloom Energy Stock",
    "Netflix Stock Down 47%: Too Early To Buy?",
    "Apple outage today: iCloud and App Store down after a week of big outages",
    "Could Vertiv Actually Climb 99%? Or Is Bernstein Totally Wrong?",
    "Robinhood Sinks 6% for a Second Day, Circle Down 5%, Affirm Down 5% as Fintech Stocks Slide",
    "Evercore Calls Arista the Clearest Beneficiary of NVIDIA's CEO Ouster",
    "Stocks slip ahead of Nvidia earnings report, Home Depot shares slide",
    "Vistra Keeps Sliding: Why BMO Sees 51% Upside",
    "Lam Research Stock Fell Nearly 7%. What Happened?",
    "Applied Optoelectronics falls 8% on $550 million stock offering plan",
    "Chip stocks fall on US semiconductor restrictions",
]
SLANG = [(r"(?i)\bsell-?offs?\b", "sharp drop"), (r"(?i)\bsells off\b", "falls sharply"),
         (r"(?i)\bbuy the dip\b", "buy on the decline"), (r"(?i)\boutages?\b", "service disruption"),
         (r"(?i)\b(stock|shares)\b", "share price")]

def sub_names(t, mode):
    for en, tick, ko in NAMES:
        t = re.sub(rf"\b{re.escape(en)}\b", ko if mode == "ko" else tick, t)
    return t

path = snapshot_download("JustFrederik/nllb-200-distilled-600M-ct2-int8")
sp_file = (glob.glob(os.path.join(path, "*.model")) or [None])[0] or snapshot_download("facebook/nllb-200-distilled-600M", allow_patterns=["sentencepiece.bpe.model"]) + "/sentencepiece.bpe.model"
sp = spm.SentencePieceProcessor(model_file=sp_file)
tr = ctranslate2.Translator(path, device="cpu", compute_type="int8")
def run(texts):
    src = [["eng_Latn"] + sp.encode(t, out_type=str) + ["</s>"] for t in texts]
    return [sp.decode(o.hypotheses[0][1:]) for o in tr.translate_batch(src, target_prefix=[["kor_Hang"]] * len(src), beam_size=4)]

def slang(t):
    for p, r in SLANG: t = re.sub(p, r, t)
    return t
variants = {"raw": TITLES, "slang": [slang(t) for t in TITLES],
            "A_ko": [slang(sub_names(t, "ko")) for t in TITLES], "B_tick": [slang(sub_names(t, "tick")) for t in TITLES]}
outs = {k: run(v) for k, v in variants.items()}
for i, t in enumerate(TITLES):
    print("\nEN    :", t)
    for k in outs:
        print(f"{k:6}:", outs[k][i], "   <-", variants[k][i] if k != "raw" else "")

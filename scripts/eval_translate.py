"""Temporary: compare free offline EN->KO models on real headlines (Fin-tip_lab)."""
import io, os, time, zipfile, glob
import requests, ctranslate2, sentencepiece as spm

TITLES = [
    "The Nasdaq-100 Is Headed for Its Worst November Since 2008",
    "US Dollar Falls Early Tuesday Ahead of Busy Economic Release Schedule",
    "Oil prices caught between rising Mideast exports and tanker attacks",
    "Morning Bid: Chip wreck",
    "Betting on chip stocks remains the most crowded trade by far, BofA's survey finds",
    "Nvidia, AI Angst Dim Wall Street Pre-Bell; Asia, Europe Off",
    "Dollar steady after five-day rally, NZ dollar at lowest since 2020",
    "Bank of America doubles down on Nvidia ahead of earnings",
    "Market Chatter: Amazon, Google, Meta Lobbying Fails to Stop Trump From Signing Order",
    "Tesla's Robo-Taxi Progress Isn't Enough to Move the Stock",
    "Samsung and Apple Reportedly Near Multi-Year DRAM Supply Agreement",
    "Federal Reserve Board announces approval of application by Huntington Bancshares",
    "Bessent Tells the Fed Not to Hike 'Too Much'",
    "Lilly agrees to acquire Calytrix Bio for $1.5bn",
    "Should You Buy SanDisk Stock After Its 50% Slide?",
]

def argos():
    t0 = time.time()
    r = requests.get("https://argos-net.com/v1/translate-en_ko-1_1.argosmodel", timeout=120)
    r.raise_for_status()
    zipfile.ZipFile(io.BytesIO(r.content)).extractall("argos")
    root = glob.glob("argos/*/")[0]
    sp = spm.SentencePieceProcessor(model_file=os.path.join(root, "sentencepiece.model"))
    tr = ctranslate2.Translator(os.path.join(root, "model"), device="cpu")
    t1 = time.time()
    out = tr.translate_batch([sp.encode(t, out_type=str) for t in TITLES], beam_size=4)
    res = [sp.decode(o.hypotheses[0]) for o in out]
    print(f"ARGOS download {t1-t0:.1f}s ({len(r.content)/1e6:.0f}MB) translate {time.time()-t1:.1f}s")
    return res

def nllb(repo):
    from huggingface_hub import snapshot_download
    t0 = time.time()
    path = snapshot_download(repo)
    sp_file = (glob.glob(os.path.join(path, "*.model")) or [None])[0]
    if sp_file is None:
        sp_file = snapshot_download("facebook/nllb-200-distilled-600M", allow_patterns=["sentencepiece.bpe.model"]) + "/sentencepiece.bpe.model"
    sp = spm.SentencePieceProcessor(model_file=sp_file)
    tr = ctranslate2.Translator(path, device="cpu", compute_type="int8")
    t1 = time.time()
    src = [["eng_Latn"] + sp.encode(t, out_type=str) + ["</s>"] for t in TITLES]
    out = tr.translate_batch(src, target_prefix=[["kor_Hang"]] * len(src), beam_size=4)
    res = [sp.decode(o.hypotheses[0][1:]) for o in out]
    print(f"NLLB {repo} load {t1-t0:.1f}s translate {time.time()-t1:.1f}s")
    return res

results = {}
for name, fn in (("argos", argos), ("nllb", lambda: nllb("JustFrederik/nllb-200-distilled-600M-ct2-int8"))):
    try:
        results[name] = fn()
    except Exception as e:
        print(name, "FAILED", repr(e)[:300])
for i, t in enumerate(TITLES):
    print("\nEN   :", t)
    for name, res in results.items():
        print(f"{name:5}:", res[i])

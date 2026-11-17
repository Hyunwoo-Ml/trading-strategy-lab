"""
Builds data/market/live.json -- the 15-minute market strip and next-FOMC
odds for the Fin-tip_lab main page -- and headlines.json beside it. Run by .github/workflows/market-live.yml,
which publishes the file on the `data-live` branch (force-pushed, one commit)
so the 15-minute refresh doesn't add commits or Pages deploys on main.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from build_market_dashboard import YahooFredSource  # noqa: E402

from sw1.market.dashboard import clean_json  # noqa: E402
from sw1.market.live import build_headlines, build_live  # noqa: E402
from sw1.market.translate import Translator, cache_from  # noqa: E402

PREVIOUS_HEADLINES = "https://raw.githubusercontent.com/{repo}/data-live/headlines.json"


def previous_translations() -> dict:
    """Translations already in the last published headlines.json (data-live)."""
    import os

    import requests

    repo = os.environ.get("GITHUB_REPOSITORY", "Hyunwoo-Ml/trading-strategy-lab")
    try:
        prev = requests.get(PREVIOUS_HEADLINES.format(repo=repo), timeout=20).json()
    except Exception:  # noqa: BLE001 -- first run, or the branch is missing
        return {}
    return cache_from([prev.get("macro"), prev.get("company")])


def main(source=None, out_path: Path | None = None, translator=None) -> int:
    out_path = Path(out_path or Path(__file__).resolve().parent.parent / "data" / "market" / "live.json")
    if source is None:
        source = YahooFredSource(pause_seconds=0)
        translator = translator or Translator(cache=previous_translations())
    live = clean_json(build_live(source))
    if not any(s.get("last") is not None for s in live["strip"]):
        print("[ERROR] no quotes loaded -- nothing written")
        return 1
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(live, ensure_ascii=False, allow_nan=False, indent=1), encoding="utf-8")
    heads = clean_json(build_headlines(source, translator=translator))
    if heads["macro"] or heads["company"]:
        (out_path.parent / "headlines.json").write_text(json.dumps(heads, ensure_ascii=False, allow_nan=False, indent=1), encoding="utf-8")
    print(f"headlines: {len(heads['macro'])} macro, {len(heads['company'])} company, errors: {heads['errors']}")
    if translator is not None:
        print(f"translated {translator.translated} new headlines ({len(translator.cache)} known)")
    fw = live["fedwatch"]
    print(f"wrote {out_path} at {live['generated_at']}; fedwatch: {fw.get('error') or fw}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(out_path=Path(sys.argv[1]) if len(sys.argv) > 1 else None))

"""
SW1 W1 PoC — yfinance로 나스닥 M7 종목의 가격/거래량/PER/PBR이
정상적으로 수집되는지 확인한다.

실행:
    python scripts/yfinance_poc.py
"""
import sys
import time

import pandas as pd
import yfinance as yf

M7_TICKERS = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA"]


def fetch_snapshot(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    hist = t.history(period="6mo")
    if hist.empty:
        raise RuntimeError(f"{ticker}: 가격 히스토리를 받지 못했습니다")

    info = t.info  # PER, PBR 등 펀더멘털은 info에서 가져옴
    last_close = hist["Close"].iloc[-1]
    avg_volume_20d = hist["Volume"].tail(20).mean()

    return {
        "ticker": ticker,
        "last_close": round(float(last_close), 2),
        "avg_volume_20d": int(avg_volume_20d),
        "per": info.get("trailingPE"),
        "pbr": info.get("priceToBook"),
        "history_rows": len(hist),
    }


def main() -> int:
    rows = []
    failures = []

    for ticker in M7_TICKERS:
        try:
            rows.append(fetch_snapshot(ticker))
            print(f"[OK] {ticker}")
        except Exception as exc:  # noqa: BLE001 — PoC 단계라 광범위 예외 허용
            print(f"[FAIL] {ticker}: {exc}")
            failures.append((ticker, str(exc)))
        time.sleep(0.5)  # 레이트리밋 완화

    df = pd.DataFrame(rows)
    if not df.empty:
        print("\n=== M7 스냅샷 ===")
        print(df.to_string(index=False))

    if failures:
        print(f"\n{len(failures)}/{len(M7_TICKERS)}개 종목 실패:")
        for ticker, err in failures:
            print(f"  - {ticker}: {err}")
        return 1

    print(f"\n전체 {len(M7_TICKERS)}개 종목 수집 성공.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

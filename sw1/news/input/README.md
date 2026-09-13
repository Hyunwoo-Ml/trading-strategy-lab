# 주간 뉴스 코멘터리 입력 (수동)

`sw1/news/scorer.py`는 자동 뉴스 피드가 없습니다 (원래 자소서 설계대로,
사용자가 직접 고른 국내 시황/뉴스 코멘터리를 붙여넣는 방식). 이 폴더가
그 수동 입력을 저장하는 곳입니다.

## 사용법

각 M7 종목별로 `{TICKER}.txt` 파일을 만들고 (예: `AAPL.txt`, `NVDA.txt`),
그 종목과 관련된 이번 주 시황/뉴스 코멘터리 텍스트를 붙여넣은 뒤 커밋하세요.
GitHub 웹 UI에서 직접 파일을 만들거나 편집해도 되고, 로컬에서 수정 후
푸시해도 됩니다.

```
sw1/news/input/
  AAPL.txt
  MSFT.txt
  GOOGL.txt
  AMZN.txt
  NVDA.txt
  META.txt
  TSLA.txt
```

파일이 없는 종목은 그냥 건너뜁니다 (`news_score`가 `None`으로 유지되고,
기술적 지표 점수만으로 계산됩니다 -- 파이프라인이 절대 실패하지 않습니다).

## 동작 방식

- `scripts/collect_daily_data.py`가 매 평일 실행될 때마다 이 폴더의 각
  파일을 읽어 `sw1.news.scorer.score_ticker_if_changed()`에 넘깁니다.
- 파일 내용이 **지난번 실행 때와 똑같으면** (해시 비교) Anthropic API를
  다시 호출하지 않고 이전 점수를 그대로 재사용합니다 -- 일주일에 한 번만
  갱신해도 평일마다 똑같은 텍스트로 API를 다시 호출해 비용이 나가는 일은
  없습니다.
- 파일 내용을 **새로 바꾸면** 다음 실행 때 자동으로 새로 스코어링됩니다.
- 스코어링 결과 캐시는 `data/news/cache.json`에 저장됩니다 (해시 + 점수 +
  카테고리별 세부 항목).

## 필요 조건

저장소 Settings → Secrets and variables → Actions에 `ANTHROPIC_API_KEY`가
등록되어 있어야 합니다. 키가 없으면 이 폴더에 텍스트를 넣어도 조용히
건너뛰고 (`news_score=None`) 파이프라인은 정상적으로 계속 돌아갑니다.

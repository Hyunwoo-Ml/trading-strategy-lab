# 매매전략 검증 시스템 (SW1 · SW2)

정량 지표와 뉴스 텍스트를 결합한 AI 기반 매매 전략 검증 시스템.

- **SW1** (`sw1/`): 정량 지표 7종 + 뉴스 감성 스코어링을 결합해 매매 기준(1차/2차 매수, 익절/손절)을 산출하는 파이프라인
- **SW2** (`sw2/`): 여러 매매 기준을 독립 모델로 등록해 매일 모의투자로 성과를 검증하는 시스템

## 진행 로드맵

2026.09.12 ~ 2026.10.31, 7주. 세부 계획은 프로젝트 로드맵 문서 참고.

## 개발 환경

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # 키 채워넣기
```

## 디렉토리 구조

```
sw1/
  data/        # 시세·재무 데이터 커넥터 (yfinance, 토스증권, KIS)
  indicators/  # RSI, MACD, 볼린저밴드, 이동평균, 거래량, PER, PBR
  news/        # 뉴스 스크립트 입력 → 카테고리 분류 → 감성 점수화 (Anthropic API)
  scoring/     # 정량+감성 통합 스코어, 매매 기준 산출
sw2/
  (W5부터 구현: 모델 레지스트리, 모의투자 시뮬레이터, 통계 비교 엔진)
tests/
scripts/       # PoC, 수동 실행 스크립트
```

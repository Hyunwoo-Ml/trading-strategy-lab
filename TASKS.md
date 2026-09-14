# 작업 트래커 (자동 진행용)

이 파일은 세션이 끊기거나(사용량 초과 등) 새 세션에서 이어서 작업할 때 맥락을
복원하기 위한 파일입니다. 완료된 항목과 남은 항목, 각 항목의 배경/설계 메모를
최대한 구체적으로 적어둡니다. 새 세션은 이 파일을 먼저 읽고, "다음 미완료
항목"부터 이어서 진행하면 됩니다.

## 사용자 표준 지침 (계속 유효함)

- "사용량 초과 될 때 까지 우선 돌려줄래? ... 초과 된 다음 다시 충전되면 바로
  다시 돌려줘. 내가 별도로 안시켜도!" — 사용량이 허용하는 한 계속 진행하고,
  막히지 않는 한 매번 확인받지 않는다. (파괴적 작업 — 실제 저장소 파일 삭제,
  금융 자산 실제 거래 등 — 만 예외적으로 사용자 승인 필요.)
- 실행 환경: 이 프로젝트의 "진짜" 실행 환경은 GitHub Actions뿐이다 (Cowork
  샌드박스는 외부 네트워크 접근이 없음). 모든 코드 변경은 브라우저 자동화로
  GitHub 웹 에디터를 통해 커밋해야 하며, 커밋 후 raw.githubusercontent.com +
  SHA-256 해시 비교로 바이트 단위 검증을 거친다.
- 로컬 파일은 `/home/claude/project` 에 미러링되어 있으며 git 저장소는 아님
  (그냥 로컬 사본). 모든 변경은 로컬에서 먼저 pytest로 검증 후 GitHub에 푸시.

## 완료된 작업

### Task #13 — 뉴스 스코어 통합 (완료, 2026-09-13/14)
- `sw1/news/scorer.py`: `score_ticker_if_changed()`, `text_hash()` — 캐시 인식
  뉴스 스코어링.
- `sw1/news/input/README.md`: 주간 뉴스 입력 가이드.
- `scripts/collect_daily_data.py`: `_collect_news_scores()` — 일일 파이프라인에
  뉴스 스코어 연동.
- `tests/test_news_scorer.py`, `tests/test_collect_daily_data.py`: 테스트 추가.
- `docs/index.html`: 대시보드 카드에 뉴스 감성 점수 표시 (준비 중 placeholder
  대체).
- 검증: `collect-daily-data` 워크플로 수동 실행 성공, `data/signals/latest.csv`
  에 `news_score` 컬럼 확인, 라이브 대시보드 스크린샷 확인.

### NaN-in-JSON 버그 수정 (완료, 2026-09-14)
- 근본 원인: 어떤 모델이 아직 거래를 한 번도 하지 않아 일일 수익률이 전부
  0.0인 경우(분산 0), `scipy.stats.ttest_ind(equal_var=False)`가 0/0 나눗셈으로
  `nan`을 반환. Python `json.dumps`는 기본적으로 `NaN`을 (스펙에 어긋나는)
  bare 토큰으로 직렬화하여 대시보드의 `JSON.parse`가 깨짐.
- `sw2/compare.py`: `compare_daily_returns()`에서 NaN → `None` 변환 (t_statistic,
  p_value 모두). docstring에 이유 설명 추가.
- `scripts/run_daily_paper_trading.py`: `comparisons_path.write_text(json.dumps(
  ..., allow_nan=False))` — 2차 방어선. 향후 다른 지표가 NaN을 만들면 조용히
  깨진 JSON을 쓰는 대신 런 자체가 시끄럽게 실패하도록.
- `tests/test_sw2_compare.py`: `test_zero_variance_both_groups_gives_none_not_nan`,
  `test_zero_variance_result_is_json_serializable_without_nan` 추가.
- 검증: `run-paper-trading` 워크플로 수동 실행(#4) 후 `data/sw2/comparisons/
  latest.json`이 `null` 로 정상 직렬화됨을 raw fetch로 확인. 라이브 대시보드의
  "모델 간 통계적 유의성 비교" 표가 에러 상태 없이 `—`로 정상 렌더링됨을 확인.

### Task #24 — 거래비용 모델링 (완료, 2026-09-14)
- `sw2/ledger.py`: `Portfolio`에 `transaction_cost_pct: float = 0.001` (10bps,
  커미션+슬리피지 스탠드인) 필드 추가. `buy()`는 `dollar_amount`는 그대로 전액
  현금에서 차감하되, 실제로 주식으로 전환되는 금액은
  `dollar_amount * (1 - transaction_cost_pct)`로 줄어듦 (나머지는 수수료).
  `sell_all()`은 `shares * price * (1 - transaction_cost_pct)`만 현금으로
  돌아옴. `Trade`에 `fee: float = 0.0` 필드 추가해 거래별 수수료 기록 (투명성
  + 대시보드/리포트에서 나중에 활용 가능).
- `scripts/run_daily_paper_trading.py`: `portfolio_to_dict`/`portfolio_from_dict`
  가 `transaction_cost_pct`를 라운드트립하도록 수정. 기존에 저장된 포트폴리오
  JSON(이 필드가 없음)은 `Portfolio` 클래스 기본값(0.001)으로 안전하게
  폴백 (raise 하지 않음, 파이프라인 전체의 graceful-degradation 패턴과 동일).
- `tests/test_sw2_ledger.py`: 기존 산술 테스트는 `transaction_cost_pct=0.0`을
  명시적으로 지정해 라운드 넘버 유지, 신규 테스트 5개 추가 (수수료가 매수
  주식 수를 줄이는지, 매도 순수익을 줄이는지, cost_pct=0이면 기존 동작과
  동일한지, 기본값이 0이 아닌지, 매수+매도 왕복이 수수료만큼 정확히
  손실나는지).
- 검증: 로컬 `python -m pytest -q` 152/152 통과 (기존 147 + 신규 5). 3개
  파일 모두 GitHub에 커밋 후 raw fetch + SHA-256으로 바이트 단위 검증 완료.
  `run-paper-trading` 워크플로 수동 실행(#5) 성공 확인, 5개 모델 전부의
  `data/sw2/portfolios/{model}.json`에 `transaction_cost_pct: 0.001`이 정상
  저장됨을 raw fetch로 확인. 이번 실행에서는 5개 모델 모두 실제 매수 신호가
  없어 트레이드가 0건이었으므로 (baseline 등 모든 모델 `cash: 100000,
  trades: []`) 실제 `Trade.fee` 값이 채워지는 것은 아직 라이브 데이터로는
  못 봤지만, 단위 테스트가 수수료 계산 로직 자체를 직접 검증하고 있고
  직렬화 라운드트립도 확인했으므로 다음에 실제 매수가 발생하면 자동으로
  올바르게 반영됨.
- 참고: 이 작업의 커밋 3개 중 앞의 2개(ledger.py, run_daily_paper_trading.py)
  는 test_sw2_ledger.py가 아직 구버전이라 GitHub Actions CI가 일시적으로
  빨간불이었음 (구버전 테스트가 `transaction_cost_pct` 기본값 변경으로 깨짐)
  — 마지막 커밋(테스트 갱신) 이후 CI 정상화됨. 다음부터는 동작 변경과 그
  동작을 검증하는 테스트를 같은 커밋에 묶어서 푸시할 것 (또는 최소한 연속
  커밋 사이 간격을 최소화).

## 남은 작업 (원래 5개 태스크 시퀀스 중 #22-25)

원래 태스크 정의(#21 이전)는 이전 세션 압축 과정에서 유실되어 한 줄 설명만
남아있음. 아래는 그 한 줄 설명을 기반으로 코드베이스를 실제로 조사해서 도출한
구체적 설계 메모.

### Task #22 — 포지션 사이징 개선 (미시작)
- 현재 상태: `scripts/run_daily_paper_trading.py`의 `TRANCHE_FRACTION = 0.05`
  (시작 자본의 5% 고정, 모든 티커/모델에 동일 적용). `sw2/ledger.py`
  모듈 docstring이 스스로 "intentionally simple... refine sizing rules once
  there's real comparison data to react to"라고 명시.
- 방향: 리스크 조정(변동성 기반, 예: ATR 또는 최근 수익률 표준편차로 포지션
  크기 역가중) 또는 티커 가중(시가총액/유동성 등) 사이징으로 개선. 최소
  변경으로는 "모델별 손절% 대비 리스크 예산 고정" 방식(예: 계좌의 X%를
  손절폭으로 나눠 주식 수 결정 — classic risk-based position sizing)이 가장
  간단하고 설명 가능함.
- 영향 범위: `sw2/ledger.py`(Portfolio.buy 시그니처는 유지, tranche 계산 로직만
  호출부에서 변경 가능) 또는 새 `sw2/sizing.py` 모듈 신설 후
  `scripts/run_daily_paper_trading.py`의 `process_model_for_day` /
  `process_price_criteria_model_for_day`에서 사용.
- 테스트: `tests/test_sw2_ledger.py` 또는 신규 `tests/test_sw2_sizing.py`에
  케이스 추가 (예: 손절폭이 클수록 매수 수량이 작아지는지).

### Task #23 — 백테스트/통계적 엄밀성 강화 (미시작, NaN 수정과는 별개)
- 현재 상태: `sw2/compare.py`의 `compare_all_pairs`가 모델 쌍마다 독립적으로
  Welch's t-test 1회씩 수행. 다중비교 보정 없음, 효과크기(effect size) 없음,
  일별 수익률의 자기상관(autocorrelation) 미고려 (t-test의 i.i.d. 가정 위반
  가능성).
- 방향:
  1. 다중비교 보정: Bonferroni 또는 Benjamini-Hochberg(FDR)를 `compare_all_pairs`
     결과에 사후 적용하는 함수 추가 (예: `adjust_for_multiple_comparisons`).
  2. 효과크기: Cohen's d를 `ComparisonResult`에 필드로 추가.
  3. 자기상관: 최소한 경고/메모 수준으로 처리 (예: Ljung-Box 검정으로 자기상관
     탐지 후 유의성 해석에 caveat 추가), 여유가 되면 block bootstrap으로 대체.
- 영향 범위: `sw2/compare.py` (ComparisonResult 필드 추가는 하위 호환 깨짐 —
  `data/sw2/comparisons/latest.json`을 읽는 `docs/index.html`도 같이 업데이트
  필요).
- 테스트: `tests/test_sw2_compare.py`에 다중비교 보정, 효과크기 계산 케이스
  추가.

### Task #25 — 거버넌스 기준 (미시작, 범위 미확정)
- 현재 상태: 모델을 "승격"하거나 "폐기"하는 기준을 정의하는 코드가 전혀 없음.
- 방향(제안): 최소 샘플 크기(예: n>=20 거래일) + 유의수준(p<0.05, Task #23의
  다중비교 보정 반영) + 연속 우위 일수 등을 조합한 규칙을 `sw2/governance.py`
  (신규)에 함수로 정의. 예: `evaluate_promotion(comparison: ComparisonResult) ->
  GovernanceVerdict` 같은 API.
- 이 작업은 Task #23(통계 보정)이 먼저 끝나야 유의성 기준을 제대로 정의할 수
  있으므로, 순서상 #23 이후에 진행하는 것을 권장.
- 테스트: 신규 `tests/test_sw2_governance.py`.

## 다음 세션이 할 일

1. 이 파일에서 "다음 미완료 항목"을 확인 (Task #24는 완료됨 — 다음은 #22
   포지션 사이징 또는 #23 통계적 엄밀성부터 시작하면 됨. #25는 #23 이후 권장).
2. `/home/claude/project`에서 로컬로 구현 + `python -m pytest -q`로 검증.
3. GitHub 웹 에디터 브라우저 자동화로 커밋 (base64 청크 방식 또는 CodeMirror
   surgical replace, 세션 요약에 기록된 기법 참고).
4. raw.githubusercontent.com + SHA-256으로 바이트 단위 검증.
5. 이 TASKS.md를 갱신해서 "완료된 작업"으로 옮기고 다시 커밋.

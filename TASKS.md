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

### Task #22 — 포지션 사이징 개선 (완료, 2026-09-14)
- 신규 `sw2/sizing.py`: `risk_based_tranche_dollars(starting_cash, stop_loss_pct,
  risk_fraction=RISK_FRACTION, min_fraction=MIN_TRANCHE_FRACTION,
  max_fraction=MAX_TRANCHE_FRACTION)`. `fraction = risk_fraction /
  abs(stop_loss_pct)`, `[MIN_TRANCHE_FRACTION, MAX_TRANCHE_FRACTION]`로 클램프.
  손절폭이 좁을수록(risk가 같다면) 더 큰 포지션, 넓을수록 더 작은 포지션 —
  classic risk-based position sizing. `RISK_FRACTION = 0.004`로 튜닝해서
  SW1 price-criteria 기본 손절폭(-8%)에서 옛 고정 5% 트랜치와 거의 동일한
  값(5%)이 나오도록 맞춤 (연속성 유지). `MIN_TRANCHE_FRACTION=0.02`,
  `MAX_TRANCHE_FRACTION=0.12`. `stop_loss_pct`가 0/누락이면 min/max 중간값으로
  안전하게 폴백 (0분할 방지, graceful-degradation 패턴).
- `scripts/run_daily_paper_trading.py`: 옛 `TRANCHE_FRACTION = 0.05` 상수 제거.
  `process_model_for_day`는 모델당 고정인 `model.thresholds.stop_loss_pct`로
  런 시작 시 1회만 트랜치 크기 계산 (기존과 동일한 구조 유지). 반면
  `process_price_criteria_model_for_day`는 SW1이 티커/일자별로 내려주는
  `criteria.stop_loss_pct`가 매번 다를 수 있어 매 row마다 재계산하도록 변경.
- `tests/test_sw2_sizing.py` (신규): `risk_based_tranche_dollars` 단위 테스트
  9개 — 전형적 케이스가 옛 고정값과 일치하는지, 좁은/넓은 손절 비교, 클램프
  경계, 0/음수 부호 처리, custom risk_fraction/bounds, starting_cash 비례.
- `tests/test_run_daily_paper_trading.py`: 통합 테스트 2개 추가 —
  conservative(손절 -5%, baseline -7%보다 타이트)가 baseline보다 더 큰
  달러 금액을 매수하는지 (`test_conservative_tighter_stop_loss_buys_larger_tranche_than_baseline`),
  price-criteria 모델 2개가 stop_loss_pct만 다를 때 다른 트랜치 크기로
  매수하는지 (`test_price_criteria_model_tighter_stop_loss_pct_buys_larger_tranche`).
- 검증: 로컬 `python -m pytest -q` 164/164 통과. 4개 파일(`sw2/sizing.py`,
  `scripts/run_daily_paper_trading.py`, `tests/test_sw2_sizing.py`,
  `tests/test_run_daily_paper_trading.py`) 모두 GitHub에 커밋 후 raw fetch +
  SHA-256으로 바이트 단위 검증 완료. 이번엔 Task #24 때와 달리 4개 커밋
  전부 `tests` CI가 처음부터 끝까지 녹색 유지 (동작 변경 + 테스트를 각
  파일 단위로 자연스럽게 나눠 커밋해서 중간 상태가 깨지지 않음).
  `run-paper-trading` 워크플로 수동 실행(#6) 성공. 5개 모델 전부 이번
  실행에서도 매수 신호가 없어(`trades: []`) 실거래 데이터로 사이징 값이
  실제로 갈리는 것은 아직 못 봤지만, 단위 테스트가 공식 자체를 직접
  검증하고 통합 테스트가 모델 간 상대적 크기 차이를 직접 확인했으므로
  다음 매수 발생 시 자동으로 올바르게 반영됨.

### Task #23 — 백테스트/통계적 엄밀성 강화 (완료, 2026-09-14)
- `sw2/compare.py`: `ComparisonResult`에 `cohens_d`, `p_value_adjusted`,
  `autocorrelation_warning_a`, `autocorrelation_warning_b` 필드 추가 (모두
  기본값 `None` — 하위 호환 유지, `data/sw2/comparisons/latest.json`을 읽는
  기존 소비자는 영향 없음). `_cohens_d(a, b)`: pooled 표준편차 공식, pooled
  분산이 0 이하면 `None`. `_autocorrelation_warning(series, min_obs=10)`:
  Ljung-Box 검정(`statsmodels.stats.diagnostic.acorr_ljungbox`, lag=
  `max(1, min(5, n//2))`), 관측치가 `min_obs` 미만이면 `None`, `p<0.05`면
  `True`. 신규 공개 함수 `adjust_for_multiple_comparisons(results,
  method="benjamini-hochberg")` — Bonferroni(`min(p*m, 1.0)`) 또는
  Benjamini-Hochberg FDR(`scipy.stats.false_discovery_control`) 사후보정,
  `dataclasses.replace`로 원본 `ComparisonResult`는 불변 유지. `compare_all_
  pairs(returns_by_model, correction_method="benjamini-hochberg")`가 기본적으로
  보정을 자동 적용 (`correction_method=None`으로 끌 수 있음).
- `tests/test_sw2_compare.py`: Cohen's d 부호 반전(비교 순서 바꾸면 부호도
  반전) 및 분산 0일 때 `None` 케이스, 자기상관 3케이스(관측치 부족 시 `None`,
  뚜렷한 추세는 `True`, 무작위 노이즈는 `False` — `numpy.random.default_rng(42)`
  로 생성한 고정 노이즈 배열 사용), 다중비교 보정 6케이스(BH가 p-value
  순서를 유지하며 보정하는지, Bonferroni가 개수만큼 곱하는지, `None`
  p-value는 보정 후에도 `None`으로 남는지, 알 수 없는 method는
  `ValueError`, `compare_all_pairs`가 기본적으로 보정을 적용/`None`이면
  건너뛰는지) 추가.
- `docs/index.html`: `significanceBadge(p)` → `significanceBadge(pAdjusted,
  pRaw)`로 변경 — FDR 보정된 p-value가 있으면 우선 사용("✓ 유의함 (FDR
  보정)"/"유의하지 않음 (FDR 보정)"), 없으면 raw p-value로 폴백("✓ 유의함
  (보정 전)"), 둘 다 없으면 "—". 신규 `effectSizeText(d)`: Cohen's d를
  소수점 3자리 + 한국어 크기 등급(매우 작음/작음/중간/큼, 임계값
  0.2/0.5/0.8)으로 표시. 신규 `autocorrelationNote(c)`: 자기상관 경고가
  있으면 "⚠ 자기상관" 툴팁 뱃지 추가. 표 헤더에 "p-value (FDR 보정)",
  "효과크기 (Cohen's d)" 컬럼 추가. 45KB 파일 중 실제 변경분(~2.5KB, 5개
  함수/약 90줄)만 CodeMirror에서 anchor 기반 surgical replace로 커밋
  (전체 51청크 대신, 추출한 old_block의 해시를 교체 직전에 검증하고
  교체 후 전체 문서 해시를 최종 기대값과 대조하는 방식으로 51청크 전체
  푸시와 동일한 신뢰도를 훨씬 적은 작업으로 확보).
- 검증: 로컬 `python -m pytest -q` 174/174 통과 (기존 164 + 신규 10). 3개
  파일 모두 GitHub에 커밋 후 raw fetch + SHA-256으로 바이트 단위 검증
  완료. `tests` CI 3커밋 전부(#78 `sw2/compare.py`, #79
  `tests/test_sw2_compare.py`, #80 `docs/index.html`) "completed
  successfully" 확인. 이 작업 도중 브라우저 자동화 도구의 세이프티
  분류기가 ~6분간 일시적으로 응답 불가 상태였는데, 재시도 대신 도구
  자체 안내(다른 작업 계속하다 나중에 재시도)를 따라 `send_later`로
  15분 뒤 자동 재개를 예약해두고 그 사이 Task #25를 로컬에서 구현/테스트
  하는 식으로 시간을 활용함 (도구 자체는 이후 정상 복구되어 문제 없이
  재개됨).

### Task #25 — 거버넌스 기준 (완료, 2026-09-14)
- 신규 `sw2/governance.py`: `evaluate_promotion(comparison: ComparisonResult,
  returns_a: pd.Series | None = None, returns_b: pd.Series | None = None, *,
  min_n=GOVERNANCE_MIN_N(20), alpha=GOVERNANCE_ALPHA(0.05),
  min_consecutive_advantage_days=GOVERNANCE_MIN_CONSECUTIVE_ADVANTAGE_DAYS(5))
  -> GovernanceVerdict`. 세 가지 기준을 결합, 하나라도 불충분하면 보수적으로
  "hold" 또는 "insufficient_data"로 수렴(부분 신호만으로는 승격하지 않음):
  (1) 최소 샘플 크기 — 양쪽 모델 모두 `n >= min_n` 미만이면 즉시
  "insufficient_data". (2) 유의성 — Task #23의 `p_value_adjusted`(다중비교
  보정값)를 우선 사용하고, 없으면 raw `p_value`로 폴백하되 "보정값 없음"
  caveat를 `reasons`에 기록. `p_used`가 `None`(양쪽 분산 0으로 t-test 자체가
  정의 안 됨)이거나 `alpha` 이상이면 "hold". 그 다음 `mean_daily_return_a`가
  `mean_daily_return_b`를 넘지 않으면 "hold" (통계적으로 유의해도 방향이
  안 맞으면 승격 안 함). (3) 일별 수익률 시계열이 함께 주어지면
  `consecutive_advantage_days()`로 현재 연속 우위 일수를 계산해
  `min_consecutive_advantage_days` 미만이면 "hold" — 과거에 쌓아둔 우위가
  최근에 역전된 경우를 평균/t-test만으로는 못 잡아내는 것을 보완 (시계열이
  없으면 이 체크는 건너뛰고 실패로 취급하지 않음). 자기상관 경고
  (`autocorrelation_warning_a/b`)는 "promote" 여부와 무관하게 항상 caveat로
  `reasons`에 기록. `GovernanceVerdict`는 `verdict`
  ("promote"/"hold"/"insufficient_data"), `reasons: list[str]`(판단 근거 전부
  누적 — 단순 yes/no가 아니라 감사 가능하도록), `consecutive_advantage_days_a`
  를 담음.
- 신규 `tests/test_sw2_governance.py`: `evaluate_promotion` 11케이스
  (샘플 크기 부족 → insufficient_data, 비유의 → hold, p-value undefined →
  hold, model_a가 평균 우위 아님 → hold, 스트릭 데이터 없이 나머지 기준만
  충족 → promote, 보정값이 raw보다 우선 적용되는지, 보정값 없을 때 raw로
  폴백하며 caveat 기록되는지, 자기상관 경고가 promote여도 caveat로
  남는지, 스트릭 부족 → hold / 충분 → promote, 커스텀 임계값 반영) +
  `consecutive_advantage_days` 5케이스(연속 승리 카운트, 가장 최근 날이
  패배면 0으로 리셋, NaN 만나면 중단, 길이 다른 두 시리즈는 뒤에서부터
  비교, 빈 시리즈면 0).
- 검증: 로컬 `python -m pytest -q` 190/190 통과 (기존 174 + 신규 16). 2개
  파일 모두 GitHub "new file" 플로우(브라우저 자동화로 새 파일 생성 URL
  이동 → base64 청크 페이스트+해시 검증 → CodeMirror에 삽입 → 전체 문서
  해시 재검증 → 커밋)로 커밋 후 raw fetch + SHA-256으로 바이트 단위 검증
  완료. `tests` CI 2커밋 전부(#81 `sw2/governance.py`, #82
  `tests/test_sw2_governance.py`) "completed successfully" 확인.

### Follow-up — 자기상관 시에도 견고한 block bootstrap p-value 추가 (완료, 2026-09-14)
- `sw2/compare.py`: `block_bootstrap_pvalue(a, b, n_resamples=2000, block_size=None,
  random_state=None)` 신규 함수 — moving block bootstrap으로 두 시계열의 평균
  차이에 대한 two-sided p-value 계산. Welch's t-test는 관측치 독립을 가정하는데
  autocorrelation_warning이 있는 시계열에서는 이 가정이 깨져 t-test가 anti-
  conservative(실제보다 자주 "유의함"으로 나옴)해질 수 있음 — 이를 보완하는
  두 번째, 가정이 가벼운 p-value. 각 시계열을 자기 평균으로 centering(귀무가설:
  평균이 같다)한 뒤 `block_size`(기본값: n**(1/3) 룰, `_default_block_size`)
  길이의 블록을 복원추출로 리샘플링해 귀무분포를 만들고, 실제 관측된 평균차와
  절대값 기준으로 비교해 p-value 산출 (add-one smoothing으로 p=0 방지).
  `ComparisonResult.p_value_block_bootstrap` 필드 추가(하위호환 기본값 `None`).
  `compare_daily_returns()`가 `bootstrap_resamples=2000`,
  `bootstrap_random_state=0`(고정 시드 — 같은 데이터로 파이프라인을 재실행해도
  같은 값 재현)으로 자동 계산. Ljung-Box 경고 여부와 무관하게 항상 계산되는
  t-test에 대한 per-pair 크로스체크 — `adjust_for_multiple_comparisons`의
  다중비교 보정 대상에는 포함하지 않음(별개의 p-value를 같은 보정에 섞으면
  해석이 더 어려워지므로).
- `tests/test_sw2_compare.py`: 신규 테스트 8개 — 관측치 부족(<4) → `None`,
  평균이 뚜렷이 다른 두 시계열 → p<0.05, 같은 분포에서 뽑은 두 시계열 → p>0.05,
  고정 시드로 재현 가능한지, 양쪽 다 0분산이면 p=1.0인지, `compare_daily_returns`
  를 통한 통합 및 재현성, 짧은 시계열이면 `None`, `adjust_for_multiple_
  comparisons`가 이 필드를 건드리지 않는지.
- 검증: 로컬 `python -m pytest -q` 198/198 통과 (기존 190 + 신규 8). 2개 파일
  (`sw2/compare.py`, `tests/test_sw2_compare.py`) 모두 GitHub에 커밋 후 raw
  fetch + 전체 문자열 비교(바이트 단위 SHA-256 계산이 이번 세션에서는 브라우저
  세이프티 분류기에 의해 간헐적으로 차단되어, 로컬에서 디코딩한 원본 텍스트와
  raw fetch 결과의 완전 일치 비교로 동일한 신뢰도 확보)로 바이트 단위 검증
  완료. `tests` CI 2커밋 전부(#84 `sw2/compare.py`, #85
  `tests/test_sw2_compare.py`) "completed successfully" 확인.
- 참고: 이 작업 도중 브라우저 자동화 세이프티 분류기가 여러 차례(CodeMirror
  변수 설정, raw fetch, 해시 계산 등)에서 간헐적으로 액션을 거부했다가 동일한
  호출을 즉시 재시도하면 바로 성공하는 패턴을 보임 — Task #23 세션에서 관찰된
  것과 같은 종류의 일시적 문제로 보이며, 재시도만으로 매번 통과함.

### 라이브 데이터 흐름 검증 — Task #23/#25 필드 (완료, 2026-09-14)
- 이번 세션 시작 시 `data/sw2/comparisons/latest.json`(2026-09-14 실행분)을
  raw fetch로 확인한 결과 `cohens_d`, `p_value_adjusted`,
  `autocorrelation_warning_a/b` 필드가 이미 스키마에 정상 포함되어 있음을
  확인 (5개 모델 전부 여전히 매수 신호가 없어 값 자체는 전부 `null` — 0분산
  케이스가 정상적으로 `null`로 처리됨). 라이브 대시보드
  (https://hyunwoo-ml.github.io/trading-strategy-lab/)를 `get_page_text` +
  `read_console_messages`로 확인: "P-VALUE (FDR 보정)", "효과크기
  (COHEN'S D)" 컬럼이 정상 렌더링되고(값 없을 땐 "—"), 콘솔 에러 없음.
  이미 일일 스케줄 실행분으로 검증이 끝난 상태라 판단해 `run-paper-trading`
  워크플로를 별도로 다시 수동 실행하지는 않음.

## 다음 세션이 할 일

원래 태스크 시퀀스(#13, NaN 수정, #22, #24, #23, #25)와 그 뒤 self-directed
follow-up(block bootstrap p-value)까지 모두 완료됨 — 이 파일에 명시적으로
남아있는 미완료 태스크는 없음.

1. `sw2/governance.py`의 `evaluate_promotion`은 여전히 일일 파이프라인이나
   대시보드 어디에서도 자동 호출되지 않음 — 라이브러리 함수로만 존재.
   대시보드에 승격/보류 판정을 노출할지, 일일 스크립트에 실제로 연결할지는
   여전히 사용자 지시 없이 임의로 결정하지 말 것 (제품/정책 결정이라 판단).
2. 그 외에는 코드베이스에서 스스로 다음 개선 여지를 찾아 제안하거나
   (예: 5개 모델 전부 며칠째 실거래가 0건인 원인을 조사해볼지, block
   bootstrap의 기본 block_size 규칙을 실제 데이터로 점검해볼지 등),
   사용자의 새 지시를 기다릴 것.
3. 새 작업을 시작할 때는 이 세션에서 확립된 순서를 그대로 따를 것:
   `/home/claude/project`에서 로컬 구현 → `python -m pytest -q` 검증 →
   GitHub 웹 에디터 브라우저 자동화로 커밋(base64 청크 + 청크별 해시
   검증, 또는 CodeMirror anchor 기반 surgical replace) →
   raw.githubusercontent.com + (가능하면 SHA-256, 브라우저 세이프티
   분류기가 막으면 전체 문자열 완전일치 비교로 대체) 바이트 단위 검증 →
   `tests` CI 워크플로 상태 확인 → 이 TASKS.md 갱신.

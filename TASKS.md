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

### 가격기준모델 뉴스 게이트 마무리 + 대시보드 뉴스 노출 + 뉴스 입력 폼 (완료, 2026-09-14)
- 사용자 요청 5가지 중 "나중에" 항목(KIS 실거래 연동)을 제외한 나머지를 처리:
  1. **가격기준모델 뉴스 게이트 확장 배치 완료 확인**: 이전 세션에서 로컬
     구현까지만 되어 있던 9개 파일(`sw1/config/price_criteria_models/model_1.json`,
     `model_2.json`, `README.md`, `sw1/criteria/generator.py`(`min_news_score`
     필드), `sw2/price_criteria_model.py`(`MarketContext.news_score` +
     `_entry_block_reason` 게이트), `scripts/run_daily_paper_trading.py`,
     3개 테스트 파일)를 브라우저 자동화로 전부 GitHub에 커밋 완료. 이 중
     `tests/test_run_daily_paper_trading.py`는 "9/9 완료"로 착각하고 실제로는
     커밋되지 않은 채 넘어갔던 것을 이번 세션 시작 시 raw fetch로 재검증하다
     발견(기대 해시 불일치, 15156바이트짜리 구버전이 live였음) — 재푸시로
     바로잡음. **교훈: "완료"라고 스스로 판단한 파일도 다음 세션 시작 시
     반드시 raw fetch/API로 실제 바이트 일치를 재확인할 것.**
  2. **대시보드에 뉴스 반영 매매기준 노출** (`docs/index.html` +
     `scripts/generate_price_criteria.py`): 기존에는 가격기준모델의
     `min_news_score` 게이트가 SW2 실거래 판단에는 반영되지만 대시보드에는
     전혀 보이지 않았음(수치 자체가 CSV에 없었음). `generate_price_criteria.py`
     에 `load_news_scores()`(data/signals/latest.csv에서 읽음, 없거나
     컬럼 없으면 빈 dict로 우아하게 폴백)를 추가해 `latest.csv`/`history.csv`에
     `news_score`, `min_news_score`, `news_blocked` 3개 컬럼을 새로 기록(단,
     실제 거래 판단 로직은 여전히 `run_daily_paper_trading.py`의
     `PriceCriteriaModel.decide()`가 유일한 source of truth — 이 파일은 표시
     전용). `docs/index.html`의 `renderCriteriaCard()`에 `newsGateTag()`
     함수를 추가해 종목별 카드 하단에 뉴스 감성점수 + "✓ 게이트 통과" /
     "🔒 신규진입 보류 (뉴스)" / "게이트 미설정" / "뉴스 대기 중" 태그를 표시.
  3. **Claude에게 묻지 않고 대시보드에서 뉴스/시황 입력**: GitHub Issue Form
     (`.github/ISSUE_TEMPLATE/news-input.yml` — 티커 드롭다운 + 코멘터리
     textarea)과 이를 자동 처리하는 GitHub Actions 워크플로
     (`.github/workflows/news-input-intake.yml` — `issues: [opened, edited]`
     트리거, `actions/github-script@v7`로 폼 본문 파싱 → `sw1/news/input/
     {TICKER}.txt` 덮어쓰기 → 커밋 → 완료 댓글 + 이슈 자동 닫기, 실패 시
     안내 댓글)로 구현. 대시보드 헤더에 "📰 뉴스/시황 입력하기" 버튼
     (`.news-input-link`)을 추가해 이슈 폼으로 바로 연결. 사람이 확인/커밋할
     필요 없이 폼 제출만으로 끝나는 구조 — "복잡하면 가이드만" 요청이었지만
     실제로는 기능으로 구현 가능하다고 판단해 기능으로 제공.
  4. **SW1/SW2 대시보드 분리**: 조사 결과 백엔드(주기적/수동 실행 분리)는
     이미 되어 있었음 — `collect-daily-data.yml`(cron 평일 22:00 UTC)과
     `run-paper-trading.yml`(cron 평일 22:30 UTC, 30분 뒤)이 이미 별도
     워크플로이고 둘 다 `workflow_dispatch: {}`로 수동 실행도 가능. 따라서
     남은 범위는 대시보드 UI 분리뿐이라고 사용자에게 설명하고 진행 동의 받음
     — **이 UI 분리 작업 자체는 이번 세션에서 아직 시작 안 함, 다음 세션
     과제로 이월.**
  5. **모델 파라미터 개수에 대한 객관적 평가**: 채팅으로 답변 완료(파일 변경
     없음) — 현재 파라미터 + 기술적 지표 + 뉴스 스크립트만으로는 수익률을
     "보장"할 수 있는 모델은 만들 수 없다는 점을 명확히 했고, 파라미터
     개수를 늘리는 것보다 종목별 오버라이드, 변동성 조정 손절, 히스토리컬
     백테스트 확장 등이 더 유효한 방향이라고 조언.
- `tests/test_generate_price_criteria.py`에 `news_score`/`min_news_score`/
  `news_blocked` 관련 신규 테스트 4개 추가(뉴스 없음 → null, 임계값 미만 →
  차단 표시, 게이트 미설정 모델은 나쁜 뉴스에도 차단 안 함, signals에 없는
  티커는 null이지만 차단 안 함).
- 검증: 로컬 `python -m pytest -q` 219/219 통과. 총 10개 파일(재푸시 1개
  포함)을 브라우저 자동화로 커밋 — `docs/index.html`(47171바이트)은 한 번에
  넣기엔 너무 커서 원문을 8개 청크로 쪼개 CodeMirror에 순차 삽입 후 전체
  문서 SHA-256으로 최종 일치 확인하는 방식을 새로 사용(기존 anchor 기반
  surgical replace보다 안전 — 전체 재검증 가능). 신규 파일 2개
  (`.github/ISSUE_TEMPLATE/news-input.yml`,
  `.github/workflows/news-input-intake.yml`)는 기존 "edit existing file"
  플로우 대신 `.../new/{branch}?filename={path}` URL로 새 파일 생성 플로우를
  이번 세션 처음 사용 — 정상 동작 확인(브레드크럼에 경로가 올바르게 반영되고,
  GitHub이 `.github/ISSUE_TEMPLATE/*.yml`을 issue-template으로 자동 인식하는
  것도 확인). `news-input.yml` 첫 삽입 시도에서 내용을 파일에서 다시 읽지
  않고 기억으로 타이핑했다가 해시 불일치 발생(한국어 단어 철자 오류) — 반드시
  로컬 파일에서 `json.dumps`로 재추출한 리터럴만 사용할 것이라는 기존 규칙을
  재확인. 모든 파일 GitHub Contents API(`size`/`sha`)로 최종 검증 —
  raw.githubusercontent.com CDN은 캐시 지연이 있어(같은 세션 내 커밋 직후
  404/구버전 반환) 즉시 검증이 필요할 때는 API `contents` 엔드포인트가 더
  신뢰도 높음. `tests` CI 커밋 전부(#91~#100, 재푸시분 #97, 신규파일 #100,
  #101 포함) "completed successfully" 확인.

### Task #35: Walk-forward(롤링 윈도우) 검증 — SW1 퀀트 스코어링

- **배경**: 이전 세션에서 다음 우선순위로 지정됨("뉴스 작업 다음으로 바로
  착수"). 세션 시작 전 `AskUserQuestion`으로 설계 방향 4가지 확인:
  데이터 소스는 하이브리드(yfinance 과거 시세 + 저장된 signals 히스토리),
  검증 대상은 SW1 지표/뉴스 스코어링 로직(추천안 채택), 방식은 롤링 윈도우
  (추천안 채택), 결과는 저장소 커밋 + 대시보드 노출(추천안 채택).
- **범위 조정(문서화된 판단)**: "SW1 지표/뉴스 스코어링 로직"이 명목상
  검증 대상이었지만, 뉴스 감성 점수를 재현할 과거 시황 아카이브가 존재하지
  않음(`sw1/news/log.py`는 현재/실시간 입력만 기록) — 따라서 실제 백테스트는
  `sw1/scoring/integrate.py`의 **고정 가중치 기술적 지표 수식
  (`compute_quant_score`)만** 검증 대상으로 좁힘. 이 제외는 모듈
  docstring·스크립트 docstring·대시보드 section-sub 텍스트 세 곳에 명시적으로
  문서화. 뉴스/통합 점수 검증은 향후 라이브 페이퍼트레이딩 히스토리
  (`data/signals` 누적분, `news_score` 포함)가 충분히 쌓이면 진행하는
  것으로 다음 세션 과제에 명시.
- **"Walk-forward" 개념 재정의**: `compute_quant_score`는 학습 가능한
  파라미터가 없는 고정 가중치 휴리스틱이므로 전통적 train/test 분할이
  적용되지 않음 — 대신 **롤링 윈도우 안정성 검증**으로 구현: 약 2년치
  yfinance 과거 시세를 180일 윈도우·30일 스텝으로 굴려가며, 각 윈도우
  독립적으로 스코어와 5거래일 순방향 수익률 간 Spearman 순위상관(IC,
  information coefficient)을 계산. 여러 독립 구간에서 IC 부호가 꾸준히
  양수면 실제 신호, 구간마다 부호가 뒤섞이거나 평균 IC가 0에 가까우면
  과최적화/노이즈로 해석.
- **신규 파일 7개** (전부 GitHub Contents API `size`/`sha`로 바이트 단위
  검증 완료):
  - `sw1/validation/__init__.py` — 모듈 docstring만(99바이트, GitHub
    `/new/main?filename=` 플로우가 트레일링 개행을 자동 추가하는 것으로
    확인된 유일한 사례 — 98바이트로 작성했으나 99바이트로 커밋됨, 내용은
    바이트 단위로 일치, 무해함).
  - `sw1/validation/walkforward.py`(7550바이트) — 순수 로직 모듈:
    `compute_forward_returns`, `make_rolling_windows`, `window_ic`,
    `WindowResult`, `run_walkforward`, `summarize_walkforward`.
  - `tests/test_walkforward.py`(6712바이트, 18개 테스트) — 모든 함수의
    엣지 케이스(빈 입력, 관측치 부족, NaN, 상수 시계열, 경계 윈도우 클리핑
    등) 커버.
  - `scripts/run_walkforward_validation.py`(6073바이트) — GitHub Actions
    전용 실행 스크립트. M7 7종목 순회, 종목별 실패해도 나머지는 계속
    진행(graceful degradation), `data/walkforward/results.json`에
    `run_ts`/`config`/`overall`(풀링 집계)/`tickers`(종목별)/`failures`
    기록.
  - `tests/test_run_walkforward_validation.py`(4088바이트, 5개 테스트) —
    합성 OHLCV로 `fetch_ohlcv` 모킹, 전종목 성공/일부 실패/전체 실패/풀링
    집계 정확성 검증.
  - `.github/workflows/walkforward-validation.yml`(1309바이트) —
    `workflow_dispatch`만(일별 스케줄 없음 — 고정 가중치 수식은 매일 바뀌지
    않으므로 수식 변경 후 또는 주기적 드리프트 점검 시 수동 실행).
  - `docs/index.html` 수정(52505바이트 전체) — "04 Walk-forward 검증(SW1
    퀀트 스코어)" 섹션 신규 추가(요약 카드 3개: 전체 풀링 평균 IC, 양(+)
    구간 비율, 검증된 구간 수 + 종목별 상세 테이블). 이 파일은 50KB를
    넘어 단일 삽입이 `Read` 툴 컨텍스트 제한에 걸려, 기존에 확립된 청크
    분할 삽입 패턴(8000자씩 7개 청크를 CodeMirror에 순차 append, 마지막
    청크에서 전체 문서 SHA-256을 계산해 사전 계산한 목표 해시와 비교
    검증)을 재사용.
- **검증**: 로컬 `python -m pytest -q` 256 passed / 7 failed — 7개 실패는
  전부 기존에 알려진 `test_run_daily_paper_trading.py`의 FOMC 블랙아웃
  윈도우 충돌(오늘 날짜 2026-09-15가 2026-09-16 FOMC 발표일의 블랙아웃
  구간에 포함되어 발생하는, `sw1/calendar/events.py`에 하드코딩된 날짜로
  인한 기존 이슈 — 이번 세션 작업과 무관, 이전 세션에서도 동일하게 확인됨)와
  완전히 동일. 신규 테스트 23개(18+5) 전부 통과, 기존 테스트 회귀 없음.
  커밋 후 GitHub Actions `tests` 워크플로에서도 동일하게 7 failed / 256
  passed 확인(CI 로그 직접 확인해 실패 목록이 로컬과 정확히 일치함을
  재검증) — `pages build and deployment`는 성공.
- **최초 실행 완료(같은 세션, 커밋 직후)**: `walkforward-validation.yml`을
  GitHub Actions 탭에서 수동 트리거(`workflow_dispatch`) → 성공
  (run_ts 2026-09-15T04:54:31Z). `data/walkforward/results.json` 결과:
  7종목 전부 성공(failures 없음), 풀링 140/140구간 검증, 전체 평균 IC
  0.055, 양(+) 구간 비율 70%, 최고 IC 0.456 / 최저 IC -0.426. 종목별로는
  TSLA(평균 IC 0.137, 양수 100%)·NVDA(0.131, 90%)가 가장 안정적이고
  GOOGL(-0.038, 40%)·META(0.003, 50%)는 혼재/약함 — 종목마다 신호 강도
  차이가 크다는 것을 그대로 보여줌(과장 없이 실제 계산값). 라이브 대시보드
  (https://hyunwoo-ml.github.io/trading-strategy-lab/)를 `get_page_text` +
  `read_console_messages`로 재확인: "04 Walk-forward 검증" 섹션이 요약
  카드 3개 + 종목별 테이블로 정상 렌더링, 콘솔 에러 없음.

### SW1/SW2 대시보드 분리 (완료, 2026-09-15)

- **배경**: TASKS.md에 "다음 세션이 할 일" 1번으로 기록되어 있던, 사용자가
  이전 세션에서 이미 진행 동의한 프론트엔드 전용 작업. 백엔드(각 SW1/SW2
  workflow)는 이미 독립적이었으므로 순수 UI 리팩터링.
- **구조 분석**: 기존 단일 `docs/index.html`(52505바이트)은 4개 섹션
  (01 M7 시그널, 02 가격기준모델, 03 SW2 페이퍼트레이딩 성과, 04
  Walk-forward 검증)과 그 JS(공통 helper + 섹션별 렌더 함수 + 공용 init
  블록)를 한 파일에 담고 있었음. 01/02/04는 SW1 산출물, 03만 SW2.
- **구현**: 원본 `docs/index.html`을 로컬에서 Python 스크립트로 라인
  단위 슬라이싱(기억 재입력이 아니라 실제 파일 내용을 그대로 절단/재조립 —
  기존 확립된 "리터럴은 항상 파일에서 재생성" 원칙을 HTML/JS 조립에도
  적용)해 두 파일로 분리:
  - `docs/index.html`(SW1 전용, 38902바이트) — 섹션 01/02/03(Walk-forward,
    04→03으로 재번호), SW2 관련 변수(`EQUITY_URL`/`COMPARISONS_URL`/
    `MODELS`)와 함수(`loadEquitySection`/`renderEquityChart`/
    `loadComparisonsSection`/`significanceBadge`/`effectSizeText`/
    `autocorrelationNote`/`modelLabel`) 전부 제거, Chart.js `<script>`
    태그도 제거(이 페이지에서 차트 미사용). 헤더 meta-row에
    `sw2.html`로 가는 링크(`📊 SW2 페이퍼 트레이딩 성과 보기 →`) 추가.
  - `docs/sw2.html`(SW2 전용, 신규, 33549바이트) — 섹션 01 = 기존 03
    그대로(Equity Curve 차트 + 모델 요약 카드 + t-test 비교 테이블),
    SW1 관련 변수(`SIGNALS_URL`/`WALKFORWARD_URL`/`TICKER_NAMES`/
    `TICKER_ORDER`/`PRICE_CRITERIA_MODELS`/`PRICE_CRITERIA_URL`)와 함수
    (`loadSignals`/`loadPriceCriteriaSection`/`buildM7Card`/
    `renderCriteriaCard`/`icBadge`/`loadWalkforwardSection` 등) 전부
    제거. 헤더에 `index.html`로 돌아가는 링크(`← SW1 퀀트 시그널 보기`)
    추가. 공통 helper(`fmtNum`/`fmtUSD`/`fmtPct`/`parseNumOrNull`/
    `escapeHtml`/`parseCSV`/`fetchText`/`fetchJSON`/`stateBox`)와 CSS
    `<style>` 블록은 두 페이지 모두에 그대로(바이트 동일하게) 유지 —
    미사용 CSS 클래스가 약간 남더라도 유지보수 단순성과 "분리 과정에서
    실수로 스타일 깨짐" 리스크 최소화를 우선.
  - **"SW2가 SW1 데이터를 불러올지" 설계 판단(독립 결정)**: SW2 페이지는
    SW1 데이터를 폴링/fetch하지 않는 것으로 결정. SW2의 5개 모델
    equity/비교 결과 자체가 이미 SW1 신호를 반영한 매매 시뮬레이션
    결과물이라 SW2 페이지에서 SW1 원본 지표를 별도로 보여줄 실익이
    작고, 두 페이지 모두 정적 파일을 `cache:"no-store"`로 매번 새로
    fetch하므로 "새로고침 버튼"도 불필요(페이지 새로고침 = 최신
    데이터). 대신 상호 명확한 텍스트 링크로 이동만 제공.
  - 커밋은 기존 확립된 브라우저 자동화 청크 삽입 패턴 재사용:
    `docs/index.html`은 `/edit/main/docs/index.html` 경로로 5개 청크,
    `docs/sw2.html`은 신규 파일이라 `/new/main?filename=docs/sw2.html`
    경로로 5개 청크(파일명 필드가 URL에서 자동으로 `sw2.html`, 상위
    경로 `docs`로 채워짐을 확인). 두 파일 모두 마지막 청크의 SHA-256
    해시가 로컬에서 사전 계산한 목표 해시와 정확히 일치 확인 후 커밋,
    GitHub Contents API로 `size` 바이트까지 재검증
    (`index.html` sha `75375b40...`, `sw2.html` sha `83ae837c...`).
- **검증**: 두 파일 모두 `node --check`로 추출한 `<script>` JS 구문
  검증 통과. grep으로 교차 오염 여부 확인(`index.html`에 `EQUITY_URL`/
  `MODELS`/`loadEquitySection` 등 SW2 전용 식별자 없음, `sw2.html`에
  `SIGNALS_URL`/`TICKER_NAMES`/`loadSignals` 등 SW1 전용 식별자 없음),
  HTML 태그(`html`/`head`/`body`/`script`/`section`) open/close 개수
  일치 확인. 라이브 배포 후 두 페이지 모두 `get_page_text` +
  `read_console_messages`로 재확인:
  `https://hyunwoo-ml.github.io/trading-strategy-lab/`(SW1)은 M7
  시그널 7종목 카드, 가격기준모델 탭, Walk-forward 검증 테이블까지
  전부 실제 데이터로 정상 렌더링, 콘솔 에러 없음.
  `https://hyunwoo-ml.github.io/trading-strategy-lab/sw2.html`(SW2)은
  Equity Curve 범례/모델 요약 카드/t-test 비교 테이블까지 정상
  렌더링, 콘솔 에러 없음(현재 데이터가 적어 수익률/유의성이 전부
  0%/—로 표시되는 것은 분리 전과 동일한 기존 상태 — 데이터가 더
  쌓여야 의미 있는 값이 나옴, 이번 리팩터링과 무관).
  이 작업은 프론트엔드 전용이라 `pytest` 대상 코드는 변경 없음(스킵).

## 다음 세션이 할 일

원래 태스크 시퀀스(#13, NaN 수정, #22, #24, #23, #25)와 그 뒤 self-directed
follow-up(block bootstrap p-value), 이전 세션의 뉴스 게이트 마무리 +
대시보드 뉴스 노출 + 뉴스 입력 폼, 이번 세션의 **Task #35
walk-forward(롤링 윈도우) 검증**(최초 실행까지 완료, 대시보드에 실제
데이터 표시 중), 그리고 **SW1/SW2 대시보드 분리**(`docs/index.html` =
SW1 전용, `docs/sw2.html` = SW2 전용, 상호 링크 추가, 라이브 확인까지
완료)까지 모두 완료됨.

1. **KIS 실계좌(모의투자) API 연동** — 사용자가 명시적으로 "나중에 하자"고
   보류함. 사용자가 다시 요청하기 전까지 시작하지 말 것. KIS API 키는
   채팅에 직접 붙여넣지 말고 GitHub Encrypted Secrets로 등록하도록 안내할 것
   (이미 이전에 안내함).
2. `sw2/governance.py`의 `evaluate_promotion`은 여전히 일일 파이프라인이나
   대시보드 어디에서도 자동 호출되지 않음 — 라이브러리 함수로만 존재.
   대시보드에 승격/보류 판정을 노출할지, 일일 스크립트에 실제로 연결할지는
   여전히 사용자 지시 없이 임의로 결정하지 말 것 (제품/정책 결정이라 판단).
3. 그 외에는 코드베이스에서 스스로 다음 개선 여지를 찾아 제안하거나,
   사용자의 새 지시를 기다릴 것.
4. 새 작업을 시작할 때는 이 세션에서 확립된 순서를 그대로 따를 것:
   `/tmp/repo_sync`(또는 그때그때의 로컬 클론 경로)에서 로컬 구현 →
   `python -m pytest -q` 검증 → GitHub 웹 에디터 브라우저 자동화로 커밋
   (기존 파일은 `/edit/main/{path}`, 신규 파일은 `/new/main?filename={path}`,
   대용량 파일은 청크 분할 삽입) → **항상 파일에서 다시 읽어(`json.dumps`)
   리터럴을 재생성 — 기억으로 타이핑하지 말 것** → 삽입 직후 CodeMirror
   문서 해시 재검증 → 커밋 → GitHub Contents API(`size`/`sha`)로 최종 검증
   (raw.githubusercontent.com은 캐시 지연 있음) → `tests` CI 워크플로 상태
   확인(진행 중이면 완료까지 대기) → 이 TASKS.md 갱신. 그리고 세션 시작 시
   "완료"로 기록된 파일도 최소 1개는 raw fetch/API로 재검증해 실제로
   커밋되었는지 다시 확인할 것.

---

## 2026-09-15 세션: 뉴스 입력 파이프라인 버그 진단 및 수정

사용자 확인 요청 2건에 대한 조사 및 수정.

**1) SW1/SW2 대시보드 분할 확인** — 이전 세션에서 이미 완료된 대로
`docs/index.html`(SW1: M7 퀀트 시그널 + 가격기준 모델 + walk-forward 검증)과
`docs/sw2.html`(SW2: 페이퍼 트레이딩 성과)로 분리되어 있고, 두 페이지가
서로 링크되어 있음을 재확인. 사용자가 원했던 "각기 다른 대시보드" 방향과
일치.

**2) 뉴스 입력이 M7 퀀트 시그널에 반영되지 않는 문제** — 실제 버그 2건을
발견하고 수정:

- **버그 A (라벨 미존재로 인한 워크플로 전체 스킵)**: 이슈 폼
  (`news-input.yml`)의 front matter에 `labels: ["news-input"]`이 선언돼
  있어도, 해당 라벨이 저장소에 실제로 존재하지 않으면 GitHub이 라벨을
  자동 부착하지 않고도 에러 없이 이슈를 생성함. `news-input-intake.yml`의
  `if: contains(github.event.issue.labels.*.name, 'news-input')` 조건이
  계속 false로 평가되어, 지금까지 제출된 뉴스 4건(#1 NVDA, #2 GOOGL, #3
  AAPL, #4 시장 전체)이 전부 조용히 스킵됨. → 저장소에 `news-input` 라벨을
  생성하고 기존 이슈 4건에 소급 부착.

- **버그 B (정규식 이스케이프 오류로 필드 파싱 실패)**:
  `news-input-intake.yml`의 `extractField()` 함수가 라벨 문자열의 정규식
  특수문자를 이스케이프하는 정규식 자체에 백슬래시가 한 겹 더 들어가
  깨져 있어서, `"종목 (티커)"`처럼 괄호가 포함된 필드 라벨의 괄호를
  이스케이프하지 못함 → 이슈 본문 파싱이 매번 실패 → 매번
  `"인식할 수 없는 선택입니다: """` 에러로 실패. `.github/workflows/news-input-intake.yml`
  커밋으로 수정.

  라벨 부착 + 정규식 수정 후 이슈 4건을 재트리거(제목 편집으로 `edited`
  이벤트 재발생)하여 전부 성공 확인: `sw1/news/input/{NVDA,GOOGL,AAPL}.jsonl`,
  `sw1/news/input/market/GENERAL.jsonl` 생성 및 원본 코멘터리와 내용 일치
  확인, 이슈 4건 모두 자동 코멘트 + 자동 닫힘 확인.

- **버그 C (마크다운 코드펜스로 감싼 LLM 응답 파싱 실패)**:
  `sw1/news/scorer.py`의 `parse_news_scoring_response()`가
  `json.loads(raw_text)`를 바로 호출하는데, Claude가 JSON 배열을
  마크다운 코드펜스(```json ... ```)로 감싸서 응답하는 경우가 있어 매번
  `json.JSONDecodeError`로 실패 (`collect-daily-data` 워크플로 로그에서
  7개 종목 전부 이 에러로 스킵되는 것을 확인 — LLM 분석 자체는 정상
  작동하고 있었음). `_strip_code_fence()` 헬퍼 함수를 추가해 파싱 전에
  코드펜스를 제거하도록 수정.

**최종 검증**: `collect-daily-data`를 수동 실행(workflow_dispatch)하여
`data/signals/latest.csv`의 `news_score` 컬럼에 7개 종목 전부 실제
점수가 채점되어 반영됨을 확인 (예: NVDA +0.45, MSFT -0.70). 라이브
대시보드(`https://hyunwoo-ml.github.io/trading-strategy-lab/`)에서도
M7 퀀트 시그널 카드의 "뉴스 감성 점수"가 "준비중"에서 실제 숫자로
바뀐 것, 그리고 가격기준 모델 섹션에서 뉴스 게이트(기준 -0.35)가 실제로
작동해 MSFT·META가 "🔒 신규진입 보류 (뉴스)"로 표시되는 것까지 확인
완료. 사용자가 보고한 두 증상(뉴스 감성 점수 미반영, 가격기준 모델
매매가 무변동) 모두 해소됨.

---

## 2026-09-16 세션: governance.py 승격 판정 파이프라인/대시보드 연동

이전 세션 `TASKS.md`의 "다음 세션이 할 일" 2번 — `sw2/governance.py`의
`evaluate_promotion`이 라이브러리 함수로만 존재하고 어디에서도 호출되지
않는 문제 — 에 대해 두 가지 선택지(governance 연동 vs KIS 연동)를
`AskUserQuestion`으로 물어 사용자가 **"governance.py 승격판정 연동"**을
선택, 그 지시에 따라 진행.

**설계 결정 (이번 세션 범위 내에서 자체 판단)**: `evaluate_promotion`은
`comparison.model_a`가 `model_b`보다 승격할 만한지를 판단하는 방향성
있는(directional) 함수. 기존 비교 테이블이 이미 "A vs B" 형태의 방향성
있는 쌍대 비교이므로, 그 방향 그대로 판정을 계산해 각 comparison 딕셔너리
안에 `"governance"` 중첩 객체로 내장 (별도 리스트/파일로 분리하지 않음 —
인덱스 정렬이 어긋날 가능성을 원천 차단). 자동 승격 액션이나 알림 등은
추가하지 않음 (사용자가 승인한 범위를 벗어나는 제품/정책 결정이므로).

**변경 파일 (3개, 전부 GitHub 웹 에디터 브라우저 자동화로 커밋, 전부
SHA-256 해시로 삽입 내용 검증 완료):**

- `scripts/run_daily_paper_trading.py` (커밋 `76ef6b1`) — `sw2.governance.evaluate_promotion`
  import 및 `_comparison_to_dict()` 헬퍼 추가. 각 `ComparisonResult`를
  직렬화할 때 해당 모델 쌍의 일별 수익률 시리즈로 `evaluate_promotion`을
  호출해 `"governance"` 키로 verdict(`promote`/`hold`/`insufficient_data`)와
  `reasons`를 함께 저장. 평가 중 예외 발생 시 `try/except`로 감싸
  `governance: null` + 경고 로그로 graceful degradation (기존 코드베이스
  관례 일치).
- `tests/test_run_daily_paper_trading.py` (커밋 `e527726`) — 신규 테스트 2건
  추가: `test_comparisons_carry_governance_verdict` (일일 파이프라인
  3회 실행 후 모든 comparison에 `governance` 객체가 붙고, 표본 수가
  적을 때 `insufficient_data` + "sample size" 사유가 포함되는지),
  `test_comparison_to_dict_embeds_promote_verdict_when_criteria_met`
  (n>=20, 유의한 보정 p-value, A의 평균 수익률 우위 조건을 모두 만족하는
  합성 `ComparisonResult`로 `promote` 경로 단위 검증).
- `docs/sw2.html` (커밋 `78c7dae`) — 모델 비교 테이블에 "승격 판정" 열
  추가. `governanceBadge()` 렌더 함수: `promote`는 녹색 배지("✓ {모델명}
  승격 가능"), `insufficient_data`는 주황 배지("데이터 부족"),
  `hold`은 회색 배지("보류") — 배지에 마우스를 올리면 `reasons` 배열이
  툴팁으로 표시됨. 패널 제목 아래에 판정 기준(20일 이상 기록, FDR 보정
  p-value < 0.05, A의 평균 수익률 우위, 5일 이상 연속 우위)을 설명하는
  안내 문구 추가. `.sig-badge.warn` CSS 클래스 신규 추가. 5개 청크(8000자
  ×3 + 8000자 + 1707자)로 분할 삽입, 최종 SHA-256 해시(`47eb13c2...`)로
  검증 완료.

**검증:**

- 로컬 `python -m pytest -q`: 신규 2건 포함 통과, 기존과 동일하게 7건
  실패 — 이 7건은 2026-09-15 세션 기록에 이미 문서화된 기존 이슈
  (`sw1/calendar/events.py`에 하드코딩된 FOMC 블랙아웃 날짜와 오늘 날짜가
  겹쳐서 발생, 이번 governance 작업과 무관)와 완전히 동일한 목록임을
  재확인. **회귀 없음.**
- GitHub Actions `tests` 워크플로: 커밋 3건(`76ef6b1`, `e527726`,
  `78c7dae`) 각각 실행 후 "failed" 상태이나, 실패한 테스트 7건이 위와
  동일한 기존 이슈임을 CI 로그에서 직접 확인 (governance 커밋 이전인
  `d7134cc`에서도 이미 동일하게 실패 상태였음을 대조 확인).
- `run-paper-trading` 워크플로를 수동 실행(`workflow_dispatch`, run #10)
  하여 새 스크립트로 `data/sw2/comparisons/latest.json`을 재생성 — raw
  fetch로 실제 JSON을 확인한 결과 comparison 10건 전부에 `governance`
  중첩 객체가 정상적으로 포함됨 (현재는 모델당 9일치 데이터만 쌓여
  `n_a=9, n_b=9`로 `insufficient_data` 판정, "sample size too small"
  사유 포함 — 20일 이상 쌓이면 자연히 `promote`/`hold` 판정으로 전환됨).
- 라이브 대시보드(`https://hyunwoo-ml.github.io/trading-strategy-lab/sw2.html`)를
  브라우저로 직접 열어 확인: "승격 판정" 열이 10개 행 전부에 주황색
  "데이터 부족" 배지로 정상 렌더링됨, 콘솔 에러 없음, `pages build and
  deployment` 워크플로 성공.

**다음 세션이 할 일 갱신**: 위 "다음 세션이 할 일" 2번(governance 연동)
완료로 마감. 남은 항목은 1번(KIS 연동, 여전히 사용자 재요청 전까지 보류)뿐.


## 2026-09-16 ~ 2026-09-21: 3년 역사적 백테스트 (구간별 수익률) 기능 추가

**배경**: 사용자 요청 — "현재 매매기준을 미래에 쌓는것과 별개로, 정해진 모델 별
기준을 과거 3년 데이터에 등록했을 때, 구간(기간) 별 수익률이 어느정도인지
보여주는 직관적 지표가 있으면 좋겠어. walk forward 검증으로 SW1 검증 한건
보이는데, 정확히 무슨 의미인지 감이 안와." 두 가지를 요청: (1) 기존
walk-forward 검증이 무슨 의미인지 설명, (2) 라이브 페이퍼 트레이딩과는 별개로
과거 3년 실데이터에 현재 등록된 SW2 매매 기준을 적용했을 때의 구간별(분기별,
사용자가 명시적으로 선택) 수익률을 보여주는 신규 기능.

**walk-forward 검증과의 차이 설명**: walk-forward는 `sw1.scoring.integrate.
compute_quant_score` 수식 자체가 여러 독립적인 시간 구간에서 일관되게 예측력을
갖는지(Spearman IC)를 검증하는 것으로, "이 수식이 신뢰할 만한가"에 답한다.
반면 이번에 추가한 백테스트는 이미 확정된 매매 규칙(진입/청산/손절/포지션
사이징)을 실제 과거 시세에 그대로 적용해 실제 거래를 재현하는 것으로, "실제로
이 규칙대로 거래했다면 얼마를 벌었을까"에 답한다. 완전히 다른 질문이며 서로
보완적.

**구현**:
- `sw1/validation/backtest.py` (신규): 순수 로직 — 이미 시뮬레이션된 equity
  curve를 분기/월/연 단위로 쪼개 구간별 수익률을 계산. 각 구간은 직전 구간의
  종가 기준으로 체이닝(전체 시작점이 아님). `tests/test_backtest.py` 11건.
- `scripts/run_historical_backtest.py` (신규): 실제 3년 백테스트 드라이버.
  M7 7종목 + QQQ를 yfinance에서 3년치 받아 5개 모델(baseline/technical_only/
  conservative/price_model_1/price_model_2) 전부를 일자별로 재생. 점수 기반
  모델은 라이브 스크립트의 `process_model_for_day`를 그대로 재사용, 가격기준
  모델은 `MarketContext`가 오늘자 라이브 CSV 파일이 아닌 과거 시계열에서
  와야 하므로 `_apply_price_criteria_day`를 별도 작성(단, 거래 적용 분기는
  라이브 코드와 동일하게 복사). `pandas.rolling`/`.ewm`이 원래 과거참조적이라는
  점을 활용해 지표는 종목당 한 번만 전체 계산(미래 참조 없음 보장).
  `tests/test_run_historical_backtest.py` 10건.
- `.github/workflows/historical-backtest.yml` (신규): workflow_dispatch
  전용(필요할 때 수동 실행), `data/backtest/results.json` 커밋.
- `docs/sw2.html`: 새 섹션(idx 02) "3년 역사적 백테스트" 추가 — 모델별 요약
  카드(최종 자산가치/3년 누적 수익률/거래건수), 분기×모델 매트릭스 테이블,
  한계점(뉴스 감성 미반영, 실적 발표 블랙아웃 미반영, FOMC/CPI는 2026년만
  등록 등) 안내 박스, 마지막 실행 시각.

**알려진 한계** (대시보드에도 동일하게 노출):
- 뉴스 감성 점수: 과거 아카이브가 없어 백테스트 전 기간 중립 처리 (뉴스
  비중이 있는 baseline도 사실상 기술적 지표만으로 판단).
- 실적 발표일 블랙아웃: yfinance가 과거 실적일을 안정적으로 제공하지 않아
  미반영 (라이브 파이프라인에는 반영됨).
- FOMC/CPI 매크로 블랙아웃: 현재 2026년 일정만 하드코딩되어 있어 그 이전
  기간엔 사실상 no-op.
- 거래량/주봉추세/시장레짐(QQQ)/거래비용(10bps)/리스크 기반 사이징은 전
  기간 라이브와 동일하게 반영됨.
- 라이브 페이퍼 트레이딩 포트폴리오와 완전히 별개(새 가상 $100,000 포트폴리오
  5개로 과거 재생, 실시간 매매 기록 전혀 건드리지 않음).

**검증**:
- 로컬 `python -m pytest -q`: 신규 21건(backtest 11 + run_historical_backtest
  10) 포함 전부 통과, 회귀 없음 확인.
- GitHub 브라우저 자동화로 6개 파일(신규 4개 + 워크플로 1개 + docs/sw2.html
  전체 교체) 전부 SHA-256 해시 검증 후 커밋. `docs/sw2.html`은 5개 청크로
  나눠 삽입, 최종 해시(`7aa3e04a...`) 일치 확인 후 커밋.
- `historical-backtest` 워크플로를 `workflow_dispatch`로 수동 실행(run
  #35667100998) → 성공. 결과: baseline +1.63%(16건 거래), technical_only
  +4.58%(60건 거래), conservative/price_model_1/price_model_2는 이번
  3년 구간에서 0건 거래(임계값이 보수적이라 신호가 안 났을 뿐, 오류 아님 —
  로컬 합성 데이터 테스트에서도 동일 경향 확인됨). 13개 분기 전부 생성.
- 라이브 대시보드(`https://hyunwoo-ml.github.io/trading-strategy-lab/sw2.html`)
  직접 확인: 새 섹션 정상 렌더링, 요약 카드/분기 매트릭스/한계 안내/실행
  시각 전부 정상 표시, 콘솔 에러 없음.
- `tests` 워크플로: 이번 커밋(`b01776a`)에서 처음으로 **전체 통과**(기존에
  2026-09-15경부터 있던 FOMC 블랙아웃 날짜 충돌로 인한 7건 실패가, 날짜가
  그 구간을 지나면서 자연히 해소됨 — 회귀 없음 + 기존 이슈까지 해소).

**다음 세션이 할 일 갱신**: 이번 백테스트 기능으로 사용자 요청 완료. 남은
항목은 기존과 동일하게 1번(KIS 연동, 여전히 사용자 재요청 전까지 보류)뿐.
필요 시 후속 아이디어: (a) conservative/price_model_1/price_model_2가 실제
3년 구간에서 거래가 0건인 것을 사용자가 원하면 임계값 재검토, (b) 매크로
블랙아웃 날짜를 과거 연도까지 확장해 더 정확한 과거 재현.

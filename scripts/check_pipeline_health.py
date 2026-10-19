"""
Cross-cutting -- automated "is the daily pipeline still alive?" check.

collect-daily-data.yml (SW1, 22:00 UTC Mon-Fri) and run-paper-trading.yml
(SW2, 22:30 UTC Mon-Fri) both write dated rows/columns that the two
dashboards (docs/index.html, docs/sw2.html) already read to show a status
dot ("⚠ 데이터 갱신 N일째 지연") when a scheduled run stops happening --
see either file's `pipelineDaysStale()` for the reference implementation
this script deliberately mirrors: STALE_THRESHOLD_DAYS = 4 (a normal
Fri->Mon weekend gap is 3 calendar days; +1 day of holiday/CI-jitter
slack), and the same "whole UTC calendar days, ignore time-of-day"
comparison.

That dot is passive -- 현우 only sees it if he happens to open a
dashboard. This script is the active counterpart: run once a day
(including weekends, unlike the two data pipelines themselves) by
.github/workflows/pipeline-health-check.yml, which reads this script's
GITHUB_OUTPUT (`is_stale`, `title`) plus the markdown body file it writes
to open, update, or auto-close a GitHub Issue -- so a silently broken
schedule (a GitHub Actions outage, an expired secret, an uncaught
exception nobody happened to be watching for) reaches him as an actual
notification instead of requiring him to remember to check either
dashboard.

Pure operational monitoring of "did the automation run" -- never touches
trading parameters, strategy logic, or live weights.
"""
from __future__ import annotations

import csv
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_CSV = REPO_ROOT / "data" / "signals" / "latest.csv"
EQUITY_DIR = REPO_ROOT / "data" / "sw2" / "equity"

# Must match docs/sw2.html's MODELS list (the trading models only -- SPY/
# QQQ/TLT benchmark equity, if any, isn't written on the same daily
# schedule and isn't a pipeline-health signal).
MODELS = ["baseline", "technical_only", "conservative", "price_model_1", "price_model_2"]

# Keep in sync with docs/index.html and docs/sw2.html's STALE_THRESHOLD_DAYS.
STALE_THRESHOLD_DAYS = 4

# Stable marker the workflow searches open issues for -- change the text
# in both places together, or the workflow will stop finding/closing its
# own previously-opened issue.
ISSUE_TITLE = "⚠ 자동 파이프라인 갱신 지연 알림"


@dataclass
class FreshnessResult:
    ok: bool  # False only when the data source itself couldn't be read at all
    last_date: date | None  # freshest date found; None if unreadable or no rows yet
    days_stale: int | None  # None whenever last_date is None
    error: str | None = None


@dataclass
class HealthReport:
    is_stale: bool
    title: str
    body: str


def days_stale_utc(last_date: date, now: datetime | None = None) -> int:
    """Whole calendar days between `last_date` and "today", in UTC.

    Mirrors the dashboards' pipelineDaysStale(): compares date-only
    components so time-of-day never causes off-by-one flapping right
    around midnight."""
    now = now or datetime.now(timezone.utc)
    today = date(now.year, now.month, now.day)
    return (today - last_date).days


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def check_sw1_freshness(csv_path: Path = SIGNALS_CSV, now: datetime | None = None) -> FreshnessResult:
    """Reads the `run_ts` column of data/signals/latest.csv's first row --
    the same field docs/index.html's loadSignals() uses for its status dot."""
    if not csv_path.exists():
        return FreshnessResult(ok=False, last_date=None, days_stale=None, error=f"{csv_path.name} not found")
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error) as exc:
        return FreshnessResult(ok=False, last_date=None, days_stale=None, error=str(exc))
    if not rows:
        return FreshnessResult(ok=False, last_date=None, days_stale=None, error="empty signals file")
    run_ts = rows[0].get("run_ts", "")
    d = _parse_date(run_ts)
    if d is None:
        return FreshnessResult(ok=False, last_date=None, days_stale=None, error=f"unparseable run_ts: {run_ts!r}")
    return FreshnessResult(ok=True, last_date=d, days_stale=days_stale_utc(d, now))


def check_sw2_freshness(
    equity_dir: Path = EQUITY_DIR, models: list[str] = MODELS, now: datetime | None = None
) -> FreshnessResult:
    """Reads data/sw2/equity/{model}.csv for every model and takes the most
    recent `date` value seen across every readable file -- mirrors
    docs/sw2.html's loadEquitySection(), which unions every model's dates
    before picking the last one. A model file that's missing or unreadable
    is skipped (same per-model tolerance as the dashboard's per-file
    try/catch); only when EVERY model file is unreadable does this report
    ok=False, matching the dashboard's `allFailed` error state."""
    latest: date | None = None
    any_readable = False
    for model in models:
        path = equity_dir / f"{model}.csv"
        if not path.exists():
            continue
        try:
            with path.open(encoding="utf-8", newline="") as f:
                rows = list(csv.DictReader(f))
        except (OSError, csv.Error):
            continue
        any_readable = True
        for row in rows:
            d = _parse_date(row.get("date"))
            if d is not None and (latest is None or d > latest):
                latest = d
    if not any_readable:
        return FreshnessResult(ok=False, last_date=None, days_stale=None, error="no readable data/sw2/equity/*.csv files")
    if latest is None:
        # Files exist but have no parseable date rows yet -- e.g. right
        # after SW2's very first run. The dashboard treats this as "첫
        # 실행 대기" (ok, not stale/error), so this does too.
        return FreshnessResult(ok=True, last_date=None, days_stale=None)
    return FreshnessResult(ok=True, last_date=latest, days_stale=days_stale_utc(latest, now))


def build_report(
    sw1: FreshnessResult, sw2: FreshnessResult, threshold: int = STALE_THRESHOLD_DAYS
) -> HealthReport:
    problems: list[str] = []

    if not sw1.ok:
        problems.append(f"- **SW1 (collect-daily-data)**: 데이터를 읽을 수 없습니다 -- {sw1.error}")
    elif sw1.days_stale is not None and sw1.days_stale > threshold:
        problems.append(
            f"- **SW1 (collect-daily-data)**: 마지막 실행이 {sw1.last_date.isoformat()} "
            f"({sw1.days_stale}일 전)로, 임계값({threshold}일)을 초과했습니다."
        )

    if not sw2.ok:
        problems.append(f"- **SW2 (run-paper-trading)**: 데이터를 읽을 수 없습니다 -- {sw2.error}")
    elif sw2.days_stale is not None and sw2.days_stale > threshold:
        problems.append(
            f"- **SW2 (run-paper-trading)**: 마지막 실행이 {sw2.last_date.isoformat()} "
            f"({sw2.days_stale}일 전)로, 임계값({threshold}일)을 초과했습니다."
        )

    is_stale = bool(problems)
    lines: list[str] = []
    if is_stale:
        lines.append("자동화 파이프라인 중 일부가 정상적으로 갱신되지 않고 있습니다.")
        lines.append("")
        lines.extend(problems)
        lines.append("")
        lines.append(
            "GitHub Actions 탭에서 `collect-daily-data` / `run-paper-trading` 워크플로의 "
            "최근 실행 기록을 확인해 주세요."
        )
    else:
        lines.append("두 파이프라인 모두 정상 범위 내에서 갱신되고 있습니다. (이 알림은 자동 정리됩니다)")

    return HealthReport(is_stale=is_stale, title=ISSUE_TITLE, body="\n".join(lines))


def main() -> int:
    now = datetime.now(timezone.utc)
    # Pass the module-level constants explicitly (rather than relying on
    # check_sw1_freshness/check_sw2_freshness's own default parameter
    # values) so that tests can monkeypatch script.SIGNALS_CSV / EQUITY_DIR
    # and have main() actually see the patched paths -- a default
    # parameter value (`csv_path: Path = SIGNALS_CSV`) is bound once at
    # function-definition time, so monkeypatching the module attribute
    # later has no effect on it unless the caller re-reads the current
    # global value like this.
    sw1 = check_sw1_freshness(csv_path=SIGNALS_CSV, now=now)
    sw2 = check_sw2_freshness(equity_dir=EQUITY_DIR, models=MODELS, now=now)
    report = build_report(sw1, sw2)

    print(f"SW1: ok={sw1.ok} last_date={sw1.last_date} days_stale={sw1.days_stale} error={sw1.error}")
    print(f"SW2: ok={sw2.ok} last_date={sw2.last_date} days_stale={sw2.days_stale} error={sw2.error}")
    print(f"is_stale={report.is_stale}")

    body_path = Path(os.environ.get("PIPELINE_HEALTH_BODY_PATH", "pipeline_health_body.md"))
    body_path.write_text(report.body, encoding="utf-8")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"is_stale={'true' if report.is_stale else 'false'}\n")
            f.write(f"title={report.title}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

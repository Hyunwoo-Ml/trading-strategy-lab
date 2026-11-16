"""
Cross-cutting -- automated "is 현우 still keeping news/시황 입력 fresh?" check.

The quant+news blended models (baseline, conservative) fold a manually-
submitted news/시황 commentary sentiment score into their integrated score
(sw1/scoring/integrate.py's compute_integrated_score) and into the
user-defined price-criteria models' news gate (min_news_score). That score
only exists when 현우 actually submits new commentary -- via the low-
friction "📰 뉴스/시황 입력" GitHub Issue form (.github/ISSUE_TEMPLATE/
news-input.yml, auto-processed by .github/workflows/news-input-intake.yml,
no git/Claude needed) -- within NEWS_WINDOW_DAYS (14 days) of today; an
entry older than that ages out of sw1/news/log.py's scoring window and
news_score silently reverts to None (neutral quant/news blend weight,
no-op price-criteria gate) -- same as if nothing had ever been submitted.

docs/index.html already surfaces this passively, per ticker, as a
"⚠ 최근 뉴스/시황 입력 N일 전" card note once data/signals/latest.csv's
news_days_ago column (scripts/collect_daily_data.py's
_collect_news_staleness()) exceeds that window -- but that's the exact
same blind spot the SW1/SW2 pipeline-freshness status dot had before
pipeline-health-check.yml existed (see scripts/check_pipeline_health.py's
own docstring): 현우 only sees it if he happens to open the dashboard. As
of 2026-11-16 every single M7 ticker has been sitting at news_days_ago=60
(~4x the scoring window) with no active reminder ever having existed for
this specific signal. This script is the active counterpart for news
staleness specifically, following the exact same open/update/auto-close
GitHub Issue pattern as check_pipeline_health.py, run by
.github/workflows/news-staleness-check.yml (weekly, not daily -- news
input is an inherently weekly-cadence manual workflow per sw1/news/
input/README.md, so a daily reminder would just spam the same open issue
with a new comment every day for as long as it stays unresolved).

Deliberately a SYSTEM-WIDE signal, not a per-ticker one: it fires only
when even the single MOST RECENTLY updated ticker (or the shared
market-wide GENERAL log, whichever is newer -- see
_collect_news_staleness()'s own docstring) is already past the scoring
window, i.e. nothing has been submitted for ANY ticker in that long. A
mix where some tickers are fresh and others aren't is left alone on
purpose -- that's plausibly just 현우 selectively commenting on the
tickers that actually had news, not a workflow going stale.

Pure read-only diagnostic over data/signals/latest.csv's existing
news_days_ago column -- never computes a sentiment score itself, never
touches trading parameters, strategy logic, or live weights.
"""
from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sw1.news.log import NEWS_WINDOW_DAYS  # noqa: E402 -- reuse rather than re-declare the scoring window

REPO_ROOT = Path(__file__).resolve().parent.parent
SIGNALS_CSV = REPO_ROOT / "data" / "signals" / "latest.csv"

# Stable marker the workflow searches open issues for -- change the text
# in both places together, or the workflow will stop finding/closing its
# own previously-opened issue. Deliberately distinct from
# check_pipeline_health.py's ISSUE_TITLE so the two never collide/merge.
ISSUE_TITLE = "📰 뉴스/시황 입력이 오래됐습니다"

ISSUE_FORM_URL = "https://github.com/Hyunwoo-Ml/trading-strategy-lab/issues/new?template=news-input.yml"


@dataclass
class StalenessReport:
    is_stale: bool
    title: str
    body: str


def read_news_days_ago(csv_path: Path = SIGNALS_CSV) -> dict[str, int | None]:
    """ticker -> news_days_ago, straight from data/signals/latest.csv's
    existing column (scripts/collect_daily_data.py already computes and
    writes it every run; this never recomputes it). None means that
    ticker's row has a blank/unparseable value -- the same "neither its
    own log nor GENERAL has ever had a parseable entry" case
    _collect_news_staleness() returns. An unreadable/missing file returns
    {} -- scripts/check_pipeline_health.py already alerts on that
    condition (SW1 freshness) separately, so this script stays quiet
    about it rather than raising a second, redundant alert."""
    if not csv_path.exists():
        return {}
    try:
        with csv_path.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return {}

    result: dict[str, int | None] = {}
    for row in rows:
        ticker = row.get("ticker")
        if not ticker:
            continue
        raw = row.get("news_days_ago")
        if raw in (None, ""):
            result[ticker] = None
            continue
        try:
            result[ticker] = int(raw)
        except ValueError:
            result[ticker] = None
    return result


def build_report(
    days_ago_by_ticker: dict[str, int | None], threshold: int = NEWS_WINDOW_DAYS
) -> StalenessReport:
    if not days_ago_by_ticker:
        return StalenessReport(
            is_stale=False,
            title=ISSUE_TITLE,
            body=(
                "data/signals/latest.csv를 읽을 수 없어 뉴스 입력 최신성을 확인하지 못했습니다 "
                "(SW1 데이터 자체의 문제는 pipeline-health-check가 별도로 보고하므로 여기서는 "
                "조용히 넘어갑니다)."
            ),
        )

    known = {t: d for t, d in days_ago_by_ticker.items() if d is not None}
    freshest = min(known.values()) if known else None

    is_stale = freshest is None or freshest > threshold

    lines: list[str] = []
    if is_stale:
        lines.append(
            f"M7 전 종목의 뉴스/시황 입력이 {threshold}일(스코어링 윈도우) 이상 갱신되지 않고 "
            "있습니다 -- 퀀트+뉴스 통합점수 모델(baseline, conservative)의 뉴스 블렌드와 "
            "가격기준모델의 뉴스 게이트가 사실상 중립/미적용 상태로, 매매 판단에서 뉴스 요소가 "
            "빠져 있을 가능성이 높습니다."
        )
        lines.append("")
        for ticker, d in sorted(
            days_ago_by_ticker.items(), key=lambda kv: (kv[1] is None, -(kv[1] or 0))
        ):
            lines.append(f"- **{ticker}**: 입력 이력 없음" if d is None else f"- **{ticker}**: {d}일 전")
        lines.append("")
        lines.append(
            f"[📰 뉴스/시황 입력하기]({ISSUE_FORM_URL}) 이슈 폼으로 종목 하나 또는 전체 시장 "
            "코멘터리를 입력하면 사람이 git을 건드릴 필요 없이 자동으로 반영됩니다."
        )
    else:
        lines.append(
            f"뉴스/시황 입력이 스코어링 윈도우({threshold}일) 내로 정상 갱신되고 있습니다. "
            "(이 알림은 자동 정리됩니다)"
        )

    return StalenessReport(is_stale=is_stale, title=ISSUE_TITLE, body="\n".join(lines))


def main() -> int:
    days_ago = read_news_days_ago(SIGNALS_CSV)
    report = build_report(days_ago)

    print(f"news_days_ago={days_ago}")
    print(f"is_stale={report.is_stale}")

    body_path = Path(os.environ.get("NEWS_STALENESS_BODY_PATH", "news_staleness_body.md"))
    body_path.write_text(report.body, encoding="utf-8")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a", encoding="utf-8") as f:
            f.write(f"is_stale={'true' if report.is_stale else 'false'}\n")
            f.write(f"title={report.title}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

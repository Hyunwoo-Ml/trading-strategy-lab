"""
SW1 -- news input log storage.

Replaces the old "one weekly-overwrite .txt file per ticker" format with an
append-only JSONL log per ticker, plus one shared ticker-agnostic log for
market-wide commentary (e.g. "트럼프가 이란과의 협상 가능성을 언급" -- doesn't
belong to any single M7 ticker, but may matter to several).

Layout:
  sw1/news/input/{TICKER}.jsonl   -- one JSON object per line: {"date": "YYYY-MM-DD", "text": "..."}
  sw1/news/input/market/GENERAL.jsonl -- same shape, ticker-agnostic entries

Each line is appended, never overwritten -- see the GitHub Issue Form intake
workflow (.github/workflows/news-input-intake.yml), which appends one line
per submission instead of replacing the file. A malformed line is skipped
rather than crashing the whole daily pipeline (same graceful-degradation
contract as the rest of sw1).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path

NEWS_WINDOW_DAYS = 14  # how many days of past entries stay "in view" for scoring


def read_log(path: Path) -> list[dict]:
    """Reads a JSONL log file into a list of {"date", "text"} dicts.

    Missing file -> []. Malformed individual lines are skipped (not fatal)
    so one bad entry never takes down the whole daily run."""
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and "date" in entry and "text" in entry:
            entries.append({"date": str(entry["date"]), "text": str(entry["text"])})
    return entries


def append_entry(path: Path, entry_date: str, text: str) -> None:
    """Appends one {"date", "text"} entry as a new line. Creates parent dirs
    and the file itself if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"date": entry_date, "text": text}, ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_date(value: str) -> date | None:
    try:
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def filter_recent_entries(
    entries: list[dict], as_of: date, window_days: int = NEWS_WINDOW_DAYS
) -> list[dict]:
    """Keeps only entries whose date falls within [as_of - window_days, as_of].

    An entry with a missing/unparseable date is dropped rather than kept
    (fail closed -- a malformed date shouldn't silently stay "in view"
    forever). Entries are returned sorted oldest-first so the LLM sees them
    in chronological order."""
    cutoff = as_of - timedelta(days=window_days)
    kept = []
    for entry in entries:
        d = _parse_date(entry.get("date", ""))
        if d is not None and cutoff <= d <= as_of:
            kept.append(entry)
    kept.sort(key=lambda e: e["date"])
    return kept


def latest_entry_date(entries: list[dict]) -> date | None:
    """2026-09-25: returns the most recent parseable date among `entries`
    (the {"date", "text"} dicts read_log() returns), or None if none of them
    have a parseable date (including an empty list).

    Unlike filter_recent_entries, this deliberately ignores the scoring
    window -- it's for staleness reporting ("N days since the last news
    input"), which needs to keep working even after every entry has aged
    out of the window (that's exactly when a "no news yet" reminder is most
    useful). Callers pass the FULL unfiltered log here, not the output of
    filter_recent_entries."""
    dates = [d for d in (_parse_date(e.get("date", "")) for e in entries) if d is not None]
    return max(dates) if dates else None

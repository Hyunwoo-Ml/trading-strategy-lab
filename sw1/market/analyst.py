"""
SW1 -- the 목표가 column: which way analysts have moved on a ticker lately.

Input is Yahoo's upgrades/downgrades table (firm, grade change, price-target
change) and its consensus price targets. Over the last 30 days every target
raise or upgrade counts +1, every target cut or downgrade -1; the sign of
the total is the tone. Reiterations and initiations don't move it.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

WINDOW_DAYS = 30

GRADE_KO = {"up": "상향", "down": "하향", "init": "신규", "main": "유지", "reit": "유지"}
TARGET_KO = {"Raises": "목표가 상향", "Lowers": "목표가 하향", "Maintains": "목표가 유지", "Announces": "목표가 제시"}


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f > 0 else None  # drop NaN and the 0 Yahoo uses for "none"


def target_summary(actions: pd.DataFrame | None, targets: dict | None, now: datetime, spot: float | None) -> dict:
    out = {"tone": None, "raises": 0, "lowers": 0, "upgrades": 0, "downgrades": 0, "recent": [], "consensus": None}
    if actions is not None and len(actions):
        df = actions.copy()
        idx = pd.to_datetime(df.index if "GradeDate" not in df.columns else df["GradeDate"], utc=True, errors="coerce")
        df = df.assign(_when=idx).dropna(subset=["_when"])
        cutoff = pd.Timestamp(now - timedelta(days=WINDOW_DAYS))
        cutoff = cutoff.tz_convert("UTC") if cutoff.tzinfo else cutoff.tz_localize("UTC")
        df = df[df["_when"] >= cutoff].sort_values("_when", ascending=False)
        col = lambda name: df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index)
        out["raises"] = int((col("priceTargetAction") == "Raises").sum())
        out["lowers"] = int((col("priceTargetAction") == "Lowers").sum())
        out["upgrades"] = int((col("Action") == "up").sum())
        out["downgrades"] = int((col("Action") == "down").sum())
        if len(df):
            net = out["raises"] + out["upgrades"] - out["lowers"] - out["downgrades"]
            out["tone"] = "positive" if net > 0 else "negative" if net < 0 else "neutral"
        for _, r in df.head(8).iterrows():
            out["recent"].append({
                "date": r["_when"].date().isoformat(),
                "firm": r.get("Firm"),
                "grade": GRADE_KO.get(r.get("Action"), r.get("Action")),
                "to_grade": r.get("ToGrade") or None,
                "target_action": TARGET_KO.get(r.get("priceTargetAction"), r.get("priceTargetAction")),
                "prior_target": _num(r.get("priorPriceTarget")),
                "target": _num(r.get("currentPriceTarget")),
            })
    if targets:
        mean = _num(targets.get("mean"))
        out["consensus"] = {
            "mean": mean, "median": _num(targets.get("median")),
            "high": _num(targets.get("high")), "low": _num(targets.get("low")),
            "upside_pct": round((mean / spot - 1) * 100, 1) if mean and spot else None,
        }
    return out

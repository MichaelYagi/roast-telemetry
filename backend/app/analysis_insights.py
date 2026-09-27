"""What gets sent to a local language model to comment on a set of roasts.

Only summary numbers go in -- per-group averages and spread, and a short list of
recent roasts with their key figures -- never whole curves, so the request stays
small and quick.
"""
from __future__ import annotations

import json
import math
from typing import Optional

from .roast_metrics import METRICS

_METRIC_BY_KEY = {m["key"]: m for m in METRICS}

# Per group: every key figure, as count / average / spread only.
# Per individual roast: a shorter set.
ROAST_METRICS = [
    "charge_temp_c", "fc_start_time_s", "drop_time_s", "drop_temp_c", "duration_s", "dtr_pct",
    "weight_loss_pct", "color_agtron", "cupping_score", "rating",
]
KEY_METRICS = [
    "charge_temp_c", "tp_time_s", "dry_end_time_s", "fc_start_time_s", "fc_start_temp_c", "drop_time_s",
    "drop_temp_c", "duration_s", "dry_pct", "maillard_pct", "dtr_pct", "development_time_s",
    "max_ror", "weight_loss_pct", "ror_crashes", "ror_flatlines", "ror_flicks",
    "color_agtron", "cupping_score", "rating",
]
# The same handful of numbers AnalysisView.jsx's Trends/Drift tabs use --
# keeps what the model is told in sync with what a human sees on those tabs,
# rather than a separately-chosen set that could drift out of step.
DRIFT_TREND_METRICS = ["duration_s", "drop_temp_c", "development_time_s", "dtr_pct", "weight_loss_pct"]
# Same threshold as AnalysisView.jsx's Drift tab (MIN_GROUP_FOR_OUTLIERS/OUTLIER_Z_THRESHOLD).
MIN_GROUP_FOR_OUTLIERS = 5
OUTLIER_Z_THRESHOLD = 2
MIN_TREND_POINTS = 4  # below this, an earlier-half/later-half split is too noisy to report
MAX_GROUPS = 10
MAX_ROASTS = 20

# Ollama's own default window is small; ask for enough room for this prompt.
NUM_CTX = 8192

PROMPT_TEMPLATE = """You are an experienced coffee roaster looking across many of one person's drum-roaster \
COFFEE ROASTS to find patterns. Below is data drawn from those roasts: for each group, how many roasts, and the \
average ("mean") and spread (standard deviation, "sd") of each measurement, with "n" roasts having it; a list of \
roasts already flagged as unusual for their own bean; a trend summary comparing earlier vs. later roasts on the \
key repeatability numbers; then a list of the most recent individual roasts.

Units: temperatures in Celsius, times in seconds (milestone times are counted from Charge), percentages in %. \
dtr_pct is development time as a percentage of the roast (Charge to Drop). "ror" is rate of rise in Celsius per \
minute. "ror_crashes"/"ror_flatlines"/"ror_flicks" count rate-of-rise problems. color_agtron, cupping_score and \
rating are the roaster's own after-the-fact judgements when they filled them in. FLAGGED ROASTS were computed \
directly, not guessed: each is more than two standard deviations from how that roast's own bean usually roasts, \
on the named measurement ("sd" there is how many standard deviations away, signed). TREND SUMMARY compares the \
average of the earlier half of matching roasts to the later half, per measurement, ordered by date.

Write plain text (no markdown, no # headers), short paragraphs and "-" bullets where useful, covering:
1. The main patterns you can see, citing actual numbers and group names.
2. Where the roasts are inconsistent (a large sd relative to the average) and what that might mean.
3. The roasts in FLAGGED ROASTS below, by title and bean, with the measurement that triggered each -- don't go \
looking for other "standout" roasts yourself, only report what's actually flagged.
4. Any clear direction in TREND SUMMARY worth mentioning (a real change, not noise) and what it might mean \
(equipment drift, a bean that's changed, etc.) -- say so plainly if nothing there looks like a real trend.
5. Two or three specific things to try or check next.

Be honest about small samples: with fewer than five roasts in a group, say the pattern is tentative. Do not \
invent causes the data can't show, and do not make up numbers. {question}

{scope}

GROUPS (JSON):
{groups}

FLAGGED ROASTS (JSON):
{drift_flags}

TREND SUMMARY (JSON):
{trends}

RECENT ROASTS (JSON, newest first):
{roasts}

Remember: this is coffee roasting in a drum roaster. Write your analysis now."""


def _round(value):
    return round(value, 1) if isinstance(value, float) else value


def _mean_sd(values: list[float]) -> tuple[float, Optional[float]]:
    n = len(values)
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1)) if n > 1 else None
    return mean, sd


def compute_drift_flags(rows: list[dict]) -> list[dict]:
    """Same check as AnalysisView.jsx's Drift tab: any roast >= OUTLIER_Z_THRESHOLD
    standard deviations from its own bean's mean, on any of DRIFT_TREND_METRICS,
    for beans with >= MIN_GROUP_FOR_OUTLIERS finished roasts. Computed here rather
    than left for the model to eyeball from group stats, so "roasts that stand
    out" in the prompt is grounded in the same deterministic check a human sees
    on the Drift tab, not a guess reconstructed from raw numbers."""
    by_bean: dict[str, list[dict]] = {}
    for row in rows:
        by_bean.setdefault(row.get("beans") or "(none)", []).append(row)

    by_roast: dict[str, dict] = {}
    for bean, members in by_bean.items():
        if len(members) < MIN_GROUP_FOR_OUTLIERS:
            continue
        for key in DRIFT_TREND_METRICS:
            pairs = [(r, r["metrics"][key]) for r in members if r["metrics"].get(key) is not None]
            if len(pairs) < MIN_GROUP_FOR_OUTLIERS:
                continue
            mean, sd = _mean_sd([v for _, v in pairs])
            if not sd:
                continue
            for row, value in pairs:
                z = (value - mean) / sd
                if abs(z) < OUTLIER_Z_THRESHOLD:
                    continue
                entry = by_roast.setdefault(
                    row["id"], {"title": row["title"], "beans": bean, "date": row["created_at"][:10], "flags": []}
                )
                entry["flags"].append({"measurement": _METRIC_BY_KEY[key]["label"], "value": _round(value), "sd": round(z, 1)})

    flags = sorted(by_roast.values(), key=lambda f: max(abs(x["sd"]) for x in f["flags"]), reverse=True)
    return flags[:MAX_ROASTS]


def compute_trend_summary(rows: list[dict]) -> list[dict]:
    """Earlier-half vs. later-half average for each of DRIFT_TREND_METRICS,
    across the whole matching set ordered by date -- a compact stand-in for
    what the Trends tab's rolling-average line shows visually, so the model
    doesn't have to reconstruct direction from a flat list of recent roasts."""
    trends = []
    for key in DRIFT_TREND_METRICS:
        points = sorted(
            ((r["created_at"], r["metrics"][key]) for r in rows if r["metrics"].get(key) is not None),
            key=lambda p: p[0],
        )
        if len(points) < MIN_TREND_POINTS:
            continue
        mid = len(points) // 2
        earlier_avg = sum(v for _, v in points[:mid]) / mid
        later_avg = sum(v for _, v in points[mid:]) / (len(points) - mid)
        info = _METRIC_BY_KEY[key]
        trends.append({
            "measurement": info["label"], "unit": info["unit"], "n": len(points),
            "earlier_avg": _round(earlier_avg), "later_avg": _round(later_avg), "change": _round(later_avg - earlier_avg),
        })
    return trends


def build_prompt(*, groups: list[dict], rows: list[dict], group_by: str, total: int, truncated: bool,
                 filters_text: str, question: Optional[str]) -> str:
    compact_groups = []
    for g in groups[:MAX_GROUPS]:
        metrics = {
            k: {s: _round(v) for s, v in g["metrics"][k].items() if s in ("n", "mean", "sd") and v is not None}
            for k in KEY_METRICS
            if k in g["metrics"]
        }
        compact_groups.append({"group": g["key"], "roasts": g["count"], "with_rate_of_rise_flags": g["flagged_ror"], "measurements": metrics})

    recent = []
    for r in rows[:MAX_ROASTS]:
        item = {
            "title": r["title"],
            "date": r["created_at"][:10],
            "beans": r["beans"],
            "data_from": r["source"],
            **{k: _round(r["metrics"][k]) for k in ROAST_METRICS if r["metrics"].get(k) is not None},
        }
        if r.get("tasting_notes"):
            item["tasting_notes"] = r["tasting_notes"][:200]
        recent.append(item)

    scope = f"SCOPE: {total} roasts" + (" (the newest 2000 of a larger set)" if truncated else "")
    scope += f", grouped by {group_by}." if group_by != "none" else ", all in one group."
    if filters_text:
        scope += f" Filters: {filters_text}."
    if len(groups) > MAX_GROUPS:
        scope += f" Only the {MAX_GROUPS} largest of {len(groups)} groups are shown."
    if total > MAX_ROASTS:
        scope += f" Only the {MAX_ROASTS} newest roasts are listed individually."

    asked = f"\n\nThe roaster also asks: {question.strip()}\nAnswer that specifically, first." if question and question.strip() else ""
    return PROMPT_TEMPLATE.format(
        question=asked, scope=scope,
        groups=json.dumps(compact_groups, separators=(",", ":"), ensure_ascii=False),
        drift_flags=json.dumps(compute_drift_flags(rows), separators=(",", ":"), ensure_ascii=False),
        trends=json.dumps(compute_trend_summary(rows), separators=(",", ":"), ensure_ascii=False),
        roasts=json.dumps(recent, separators=(",", ":"), ensure_ascii=False),
    )
